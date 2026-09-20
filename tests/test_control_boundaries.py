"""Offline sampling budget boundaries: hand workers/plugins and native journals."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from agents.codex.summary import read_control, PROFILES, VERSION
from methods.turn_control.adapter import BUDGETS
from src.source_declarations import declarations

ROOT = Path(__file__).resolve().parents[1]


class ControlBoundaries(unittest.TestCase):
    def test_codex_native_journal_all_profiles_and_duplicate_extension(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for profile, (initial, final) in PROFILES.items():
                (root / 'control-request.json').write_text(json.dumps({'version': VERSION, 'profile': profile}))
                base = {'version': VERSION, 'initial_budget': initial, 'final_budget': final}
                rows = [dict(base, event='initialized', turn_id='turn', used_turns=0, current_budget=initial, extended=False)]
                for used in range(1, final + 1):
                    if used == initial + 1:
                        rows.append(dict(base, event='extended', turn_id=None, used_turns=initial, current_budget=final, extended=True))
                    rows.append(dict(base, event='before_sampling', turn_id=None, used_turns=used,
                        current_budget=initial if used <= initial else final, extended=used > initial))
                rows.append(dict(base, event='budget_exhausted', turn_id=None, used_turns=final, current_budget=final, extended=True))
                path = root / 'control-events.jsonl'
                path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
                self.assertEqual(read_control(root)['used_turns'], final)
                rows.insert(initial + 2, rows[initial + 1])
                path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
                with self.assertRaises(ValueError):
                    read_control(root)

    def test_trae_subclass_extends_once_and_respects_native_limit(self):
        class Native:
            def __init__(self, config): self._max_steps = config
            async def _run_llm_step(self, *args): return args
            async def _finalize_step(self, *args): pass
        namespace = declarations(ROOT / 'agents/trae/controlled_runner.py', ['ControlledTrae'], {
            'SelectedTrae': Native, 'LLMMessage': lambda **kw: kw, 'AgentState': SimpleNamespace(ERROR='error'), 'json': json})
        with tempfile.TemporaryDirectory() as temp:
            for initial, final in BUDGETS.values():
                for native_limit in (200, initial - 1):
                    worker = namespace['ControlledTrae'](native_limit, {'initial': initial, 'final': final}, Path(temp) / 'control.json')
                    execution = SimpleNamespace(success=False, agent_state='running')
                    messages = []
                    for number in range(1, min(native_limit, final) + 1):
                        step = SimpleNamespace(step_number=number)
                        asyncio.run(worker._run_llm_step(step, messages, execution))
                        asyncio.run(worker._finalize_step(step, messages, execution))
                    self.assertEqual(len(worker.control['extensions']), 1 if native_limit > initial else 0)
                    self.assertEqual(worker.control['used_turns'], min(native_limit, final))
                    self.assertEqual(worker._max_steps, min(native_limit, final))

    @unittest.skipUnless(shutil.which('node'), 'prepared Node unavailable')
    def test_opencode_real_local_plugin_counts_pending_main_only(self):
        code = '''
import plugin from MODULE;
import {readFileSync, writeFileSync} from 'node:fs';
process.env.TOKENANA_CONTROL_DIRECTORY=process.argv[1];
for (const [initial,final] of [[50,67],[52,64],[29,45]]) {
 writeFileSync(`${process.argv[1]}/control-request.json`,JSON.stringify({initial,final}));
 let id=''; let child=false;
 const user={info:{id:'u',role:'user',sessionID:'s'},parts:[]};
 const client={session:{get:async()=>({data:{id:'s',...(child?{parentID:'parent'}:{})}}),messages:async()=>({data:[{info:{id,role:'assistant',sessionID:'s',agent:'build',time:{}}}]})}};
 const hooks=await plugin({client});
 const call=()=>hooks['experimental.chat.messages.transform']({}, {messages:[structuredClone(user)]});
 child=true;id='child';await call();child=false;
 for(let i=1;i<=final;i++){id=`m${i}`;await call();await call();}
 id='blocked';let stopped=false;try{await call()}catch(e){stopped=e.message.includes('BUDGET_EXHAUSTED')}
 const state=JSON.parse(readFileSync(`${process.argv[1]}/control.json`));
 if(!stopped||state.used_turns!==final||state.extensions.length!==1)throw Error('budget/retry/subagent failure');
}
'''.replace('MODULE', json.dumps((ROOT / 'agents/opencode/turn_control.mjs').as_uri()))
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([shutil.which('node'), '--input-type=module', '-e', code, temp], check=True, capture_output=True, timeout=30)
