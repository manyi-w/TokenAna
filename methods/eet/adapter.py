"""Existing EET source-compatible retrieval and confidence guidance, not paper EET."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import re

from src.interfaces import MethodResult
from src.method_sessions import SessionMethod, save_state, tool_call, source_metrics
from src.session import SessionDecision
from src.source_declarations import declarations

ROOT = Path(__file__).parent / 'upstream'
MINI = ROOT / 'mini-swe-agent/src/minisweagent'
TRAE = ROOT / 'trae-agent/trae_agent'


def experience_store(path, source=MINI / 'experience'):
    if not Path(path).is_file():
        raise ValueError('existing EET experience library required; generation is disabled')
    model = declarations(source / 'models.py', ['Experience'], {
        'dataclass': dataclass, 'field': field, 'asdict': asdict, 'datetime': datetime, 'json': json})['Experience']
    namespace = declarations(source / 'store.py', ['TextSimilarity', 'ExperienceStore'], {
        'Experience': model, 'json': json, 'os': os, 'Path': Path, 're': re,
        'Counter': Counter, 'defaultdict': defaultdict, 'math': math})
    return namespace['ExperienceStore'](storage_path=str(path))


class EET(SessionMethod):
    version = 'eet-current-source-compatible-v1'

    def validate_options(self, options):
        if set(options) - {'experience_library', 'retrieval_scope'}:
            raise ValueError('EET only accepts an existing experience_library; no generated experiences or new stopping rules')
        if options.get('experience_library') and not Path(options['experience_library']).is_file():
            raise ValueError('existing EET experience library required')
        if options.get('retrieval_scope', 'repository') not in ('repository', 'cross_repository'):
            raise ValueError('retrieval_scope must be repository or cross_repository')
        if importlib.util.find_spec('jinja2') is None:
            raise ValueError('EET mini-compatible prompt requires prepared jinja2')

    def run(self, task, agent, workspace, options):
        self.validate_options(options)
        native = agent
        while hasattr(native, 'agent') or hasattr(native, 'adapter'):
            native = getattr(native, 'agent', getattr(native, 'adapter', native))
        kind = type(native).__name__.lower()
        from contextlib import nullcontext
        from src.telemetry import span
        root = getattr(getattr(workspace, 'artifacts', None), 'host', None)
        with span(root, 'method_prepare') if root is not None else nullcontext():
            callback = self.callback(task, workspace, options, agent_kind=kind)
        prompt = task.problem_statement
        call_options = {}
        if kind == 'trae':
            call_options['eet_system_suffix'] = callback.experience_prompt
        elif callback.experience_prompt:
            prompt += '\n\n' + callback.experience_prompt
        return MethodResult(calls=[agent.run_session(prompt, workspace, call_options, callback)])

    def callback(self, task, workspace, options, *, agent_kind='mini', template=None):
        if template is None:
            from jinja2 import Template, StrictUndefined
        else:
            Template, StrictUndefined = template, object
        ns = declarations(MINI / 'agents/experience_retrieval.py',
            ['ExperienceRetrievalAgentConfig', 'ExperienceRetrievalAgent'], {
                'dataclass': dataclass, 'AgentConfig': object, 'DefaultAgent': object,
                'Template': Template, 'StrictUndefined': StrictUndefined, 'Path': Path,
                're': re, 'logger': logging.getLogger('tokenana.eet')})
        source_class = ns['ExperienceRetrievalAgent']

        class NativeExperience(source_class):
            def execute_action(self, action):
                result = workspace.execute(['bash', '-c', action['action']], timeout=60)
                return {'returncode': result.returncode, 'output': result.stdout}

        expert = NativeExperience.__new__(NativeExperience)
        expert.config = ns['ExperienceRetrievalAgentConfig']()
        library = options.get('experience_library') or (
            TRAE / 'prompt/extracted_experiences_summarized_merged.jsonl' if agent_kind == 'trae' else
            MINI / 'experience/extracted_experiences_summarized.jsonl')
        expert.experience_store = experience_store(library, TRAE / 'experience' if agent_kind == 'trae' else MINI / 'experience')
        expert.workflow_progress = dict(steps=0, has_code_changes=False, test_passed=False,
                                       last_confidence_check=None, confidence_score=None)
        cross_repository = options.get('retrieval_scope') == 'cross_repository'
        if agent_kind == 'trae':
            found = expert.experience_store.search_by_issue_similarity(task.problem_statement, top_k=1, min_similarity=.15, use_tfidf=True)
            if not found:
                found = expert.experience_store.search_by_issue_similarity(task.problem_statement, top_k=1, min_similarity=.15, use_tfidf=False)
            # Original Trae extracts the repository prefix before the final issue number.
            trae = declarations(TRAE / 'agent/trae_agent.py', ['TraeAgent'], {
                'BaseAgent': object, 'override': lambda value: value, 'TRAE_AGENT_SYSTEM_PROMPT': ''})['TraeAgent']
            renderer = trae.__new__(trae)
            repo = None if cross_repository else renderer._extract_repo_from_issue_id(task.instance_id)
            found = [(exp, score) for exp, score in found if not repo or renderer._extract_repo_from_issue_id(exp.issue_id) == repo][:1]
            experience_prompt = renderer.get_system_prompt(found)
        else:
            found = expert.retrieve_experiences(task.problem_statement, None if cross_repository else task.instance_id)
            experience_prompt = expert.format_experiences(found)
        state = {'version': self.version, 'implementation': 'current-source-compatible; not full paper early stopping',
                 'agent': agent_kind, 'retrieval_scope': options.get('retrieval_scope', 'repository'),
                 'task_id': task.instance_id, 'experience_library': str(library),
                 'retrieved': [exp.issue_id for exp, _ in found],
                 'experience_hit': bool(found), 'submission_prompts': 0, 'confidence_prompts': 0,
                 'historical_experience_generation_cost': None, 'workflow_progress': expert.workflow_progress}
        seen = set()

        def adapt_completion(prompt):
            if not prompt:
                return prompt
            if agent_kind not in ('mini', 'minisweagent') or getattr(workspace, 'patch_base_commit', None):
                prompt = prompt.replace('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && ', '')
                prompt += ('\nCommit the intended source changes as required by this task, then use your native completion interface.'
                           if getattr(workspace, 'patch_base_commit', None) else '\nAfter preparing the source diff, finish through your native completion interface.')
            return prompt

        def callback(event):
            reminders = []
            if event.kind == 'after_tool' and found and agent_kind != 'trae':
                calls, response = {}, ''
                for message in event.history:
                    if message.role == 'assistant':
                        if message.text:
                            response = message.text
                        calls.update({item['id']: item for item in tool_call(message)})
                    if not message.tool_result or message.id in seen:
                        continue
                    seen.add(message.id)
                    call = calls.get(message.tool_result, {'function': {}})['function']
                    arguments = call.get('arguments', '{}')
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                    action = arguments.get('command', arguments.get('cmd', ''))
                    native = message.native.get('message', {})
                    # Unknown exit status never counts as a passing test.
                    output = {'output': message.text, 'returncode': native.get('returncode',
                              native.get('extra', {}).get('returncode', 1))}
                    expert.workflow_progress['steps'] += 1
                    expert._assess_workflow_progress(action, output)
                    if ('git add' in action.lower() or 'complete_task_and_submit_final_output' in action.lower()) and 'git diff' in action.lower():
                        valid, reason = expert._validate_fix_logic(message.text)
                        if not valid:
                            reminders.append(f'**Submission Validation Failed**: {reason}\nPlease check and fix the issue before resubmitting.')
                    score = expert._extract_confidence_score(response)
                    guidance = ''
                    if score is not None:
                        expert.workflow_progress.update(confidence_score=score,
                            last_confidence_check=expert.workflow_progress['steps'])
                        guidance = expert._get_submission_prompt(score)
                    if guidance:
                        state['submission_prompts'] += 1
                        reminders.append(adapt_completion(guidance))
                    elif expert._is_key_step(action, output):
                        state['confidence_prompts'] += 1
                        reminders.append(expert._get_confidence_check_prompt())
            state['no_trigger'] = state['submission_prompts'] == 0
            save_state(event, 'eet', state)
            return SessionDecision(state=state, reminder='\n\n'.join(reminders) or None)
        callback.experience_prompt = experience_prompt
        return callback

    def original_accounting(self, cases):
        report = source_metrics(cases, rule='eet-current-source-native-usage-compatible-v1')
        report['note'] += ' Existing library generation cost unknown; no paper-level early-stop or candidate-selection claim.'
        return report
