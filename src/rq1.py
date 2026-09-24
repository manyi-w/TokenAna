"""Offline RQ1 archive replay; never execute agents or invent missing API usage."""
import argparse
from collections import Counter
import csv
import importlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import tomllib

from .accounting import CaseUsage, OriginalCase
from .accounting_v2 import corrected_v2
from .tabular import csv_file, markdown, write_text

ROOT = Path(__file__).resolve().parents[1]
MISSING_API = 'Author archive lacks per-call raw API usage, actual cache details and complete retry/helper coverage.'
MISSING_CACHE = 'Actual cache read/write counts were not retained for the original selected calls; estimates are not measurements.'


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def archive_documents(config, destination, member_key='member_directory'):
    """Extract only the selected configuration into disposable working storage."""
    executable = shutil.which('bsdtar') or shutil.which('7zz') or shutil.which('7z')
    if executable is None:
        raise RuntimeError('Prepare bsdtar or 7z to read the existing author archives; no dependency is installed automatically.')
    archive = ROOT / config['archive']
    prefix = config[member_key] + '/'
    if Path(executable).name == 'bsdtar':
        names = subprocess.check_output([executable, '-tf', str(archive)], text=True).splitlines()
        selected = [n for n in names if n.startswith(prefix) and n.endswith('.json')]
        for name in selected:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or str(path.parent) != prefix.rstrip('/'):
                raise ValueError('Unexpected archive member path')
        if not selected:
            raise ValueError(f'No archived trajectories at {prefix}')
        subprocess.run([executable, '-xf', str(archive), '-C', str(destination), *selected],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        subprocess.run([executable, 'x', str(archive), '-o' + str(destination), prefix + '*.json', '-y'],
                       check=True, stdout=subprocess.DEVNULL)
    folder = destination / prefix
    return {p.stem: load_json(p) for p in sorted(folder.glob('*.json'))}


def native_cases(documents, config):
    # These task aggregates feed ONLY the original reader. They are never
    # promoted to corrected API observations or assigned invented response IDs.
    from .accounting_trace import atom, calc, constant
    result = []
    for case, data in documents.items():
        source = config['archive'] + '::' + config['member_directory'] + '/' + case + '.json'
        result.append(OriginalCase(case, [{'type': 'turn.completed', 'usage': {
            'input_tokens': data['metrics']['prompt_tokens'], 'output_tokens': data['metrics']['completion_tokens']},
            '_calculation': {name: atom(data['metrics'][field], source, '/metrics/' + field,
                kind='native_aggregate', description='作者任务级原汇总，并非逐调用 API usage', case_id=case)
                for name, field in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))}}], None,
            method_data={'metrics': data['metrics'], '_source': source,
                '_sources': {'case_id': {'source': config['task_source'], 'locator': 'instance_id=' + case},
                             'source_error': {'source': source, 'locator': '/result/gen'}},
                '_source_error_calculation': calc('select', calc('equal', atom(data['result']['gen'], source, '/result/gen'), constant("err-<class 'openai.APIStatusError'>", 'methods/agent_diet/adapter.py:original_accounting')), constant('APIStatusError', 'methods/agent_diet/adapter.py:original_accounting'), constant(None, 'methods/agent_diet/adapter.py:original_accounting'), description='原错误字符串映射为补偿条件'),
                'source_error': 'APIStatusError' if data['result']['gen'] == "err-<class 'openai.APIStatusError'>" else None}))
    return result


def check_population(documents, task_ids, config):
    if set(documents) != set(task_ids):
        raise ValueError(f"{config['method']}: archived cases differ from the original population")
    expected = config.get('recorded_method_options')
    if expected and any(data['metrics']['analysis_args'] != expected for data in documents.values()):
        raise ValueError(f"{config['method']}: archived method configuration differs from the RQ1 specification")


def archive_rows(documents, config):
    return [dict(task_id=case, source=config['archive'] + '::' + config['member_directory'] + '/' + case + '.json',
                 original_counted=True, input=data['metrics']['prompt_tokens'] +
                 (200_000 if config['method'] == 'agent_diet' and
                  data['result']['gen'] == "err-<class 'openai.APIStatusError'>" else 0),
                 output=data['metrics']['completion_tokens'], cache=data['metrics'].get('cached_tokens'),
                 resolved=data['result']['val'] == 'pass', generation_result=data['result']['gen'],
                 evaluation_result=data['result']['val'], native_cost_tokens=data['metrics']['cost_tokens'],
                 native_total_residual=data['metrics']['cost_tokens'] - data['metrics']['prompt_tokens'] -
                                       data['metrics']['completion_tokens'])
            for case, data in documents.items()]


def usage_field_counts(documents):
    found = Counter()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('usage', 'usageMetadata', 'prompt_tokens_details', 'completion_tokens_details',
                           'cache_read_input_tokens', 'cache_creation_input_tokens'):
                    found[key] += 1
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for data in documents.values():
        visit(data)
    return dict(found)


def paper_reference(config, report, documents, destination):
    from .accounting_trace import atom, calc, constant, expression, number
    if config['method'] == 'attn_compress':
        with (ROOT / config['original_table']).open(newline='') as stream:
            saved = next(row for row in csv.DictReader(stream) if row['config'] == 'play_gemini3flash_attncompess')
        denominator = calc('count', *(atom(case, config['task_source'], 'instance_id=' + case, kind='selection') for case in documents))
        unit = constant(1000, config['original_reader'] + ':1000', '原表千 token 单位换算')
        calculation = {label: calc('divide', calc('divide', expression(report['original']['metrics'][metric]), denominator), unit)
                       for label, metric in (('Input', 'input'), ('Output', 'output'), ('Cache', 'cache_read'))}
        calculation['Cost_Total'] = calc('divide', report['calculation']['cost'], denominator)
        calculation['pass%'] = calc('multiply', report['calculation']['original_success_rate'],
                                   constant(100, config['original_reader'] + ':100', '比例转百分比'))
        expected = {k: float(number(v)) for k, v in calculation.items()}
        matches = {k: abs(float(saved[k]) - value) < 1e-8 for k, value in expected.items()}
        if not all(matches.values()):
            raise ValueError('AttnCompress replay differs from the preserved original CSV')
        return {'saved_csv_row': saved, 'matches': matches, 'calculation': calculation,
                'input_output_cache_unit': 'mean kilotokens per task'}
    if config['method'] == 'agent_diet':
        # This reuses a historical baseline. It never generates a new baseline.
        baseline = archive_documents(config, destination, 'baseline_member_directory')
        if set(baseline) != set(documents):
            raise ValueError('AgentDiet historical baseline population differs from method')
        from methods.agent_diet.historical import replay as original_replay
        baseline_config = {**config, 'member_directory': config['baseline_member_directory']}
        baseline_original = original_replay(baseline, native_cases(baseline, baseline_config), baseline_config)
        incoming = expression(baseline_original['original']['metrics']['input'])
        cost = baseline_original['calculation']['main_cost']
        # Both original I and O divide by baseline INPUT, including its sign
        # space and three-decimal formatting. O is not an output-saving ratio.
        calculation = {key: calc('format3', calc('divide', value, denominator), description='原表保留符号空格、三位小数',
                                rule=config['original_reader'] + ':export')
                       for key, value, denominator in (
                           ('I', expression(report['original']['metrics']['input']), incoming),
                           ('O', expression(report['original']['metrics']['output']), incoming),
                           ('$', report['calculation']['main_cost'], cost),
                           ('+$', report['calculation']['helper_cost'], cost))}
        calculation['T$'] = calc('round3', calc('divide', report['calculation']['cost'], cost),
                                 description='原总费用相对 baseline，转 float 后 round 到三位', rule=config['original_reader'] + ':export')
        ratios = {key: node['value'] for key, node in calculation.items()}
        notebook = load_json(ROOT / config['original_reader'])
        cell = next(c for c in notebook['cells'] if c['cell_type'] == 'code' and
                    "export(subjs_eval_200" in ''.join(c.get('source', [])) and
                    "'llm_gemini25pro'" in ''.join(c.get('source', [])))
        saved = ''.join(''.join(o.get('text', [])) for o in cell.get('outputs', []) if o.get('name') == 'stdout')
        saved_rows = {parts[0].strip(): parts[-1].strip() for line in saved.splitlines()
                      if len(parts := line.split('\t')) == 3}
        matches = {key: key in saved_rows and float(value) == float(saved_rows[key])
                   for key, value in ratios.items()}
        if not all(matches.values()):
            raise ValueError('AgentDiet replay differs from the preserved original notebook table')
        return {'current_exporter_ratios': ratios, 'saved_notebook_output': saved, 'matches': matches, 'calculation': calculation,
                'note': 'Original I and O both divide by baseline input. Preserve that denominator; O is not output relative to baseline output.'}
    return None


def cache_correction(original, method):
    """Cache-only diagnostic on the exact original scope; not a legacy policy."""
    from .accounting_trace import calc, expression, missing
    original = original or {}
    native = original.get('original', {}).get('metrics', {})
    def metric(node, basis):
        return dict(sum=node['value'], complete=node['value'] is not None, basis=basis, calculation={'sum': node})
    def scope(incoming, outgoing, basis):
        absent = lambda: metric(missing('缺少原调用实际缓存读写，估计不可代替实测'), MISSING_CACHE)
        result = dict(input=metric(incoming, basis), output=metric(outgoing, '缓存专项保留同范围原 output'),
                      ordinary_input=absent(), cache_read=absent(), cache_write=absent(), cache=absent())
        result['total'] = metric(calc('sum', incoming, outgoing, description='缓存专项 input + 原 output'), '缓存是 input 子项，不重复相加')
        return result
    incoming = expression(native.get('input', {})) if method == 'attn_compress' else missing('原输入汇总不足以恢复实际缓存分解')
    scopes = {'main': scope(incoming, expression(native.get('output', {})),
                           'Attn 原 prompt_tokens 已含缓存，缓存估计只影响费用' if method == 'attn_compress' else '原输入缓存分解未知')}
    if method == 'agent_diet':
        helper = original.get('original', {}).get('helper_calculation', {})
        scopes['method_auxiliary'] = scope(helper.get('before', missing('原辅助输入缺失')),
            helper.get('output', missing('原辅助输出缺失')), '恢复原完整辅助输入，取消 492 × 分析次数的假定扣除；不是实测缓存量')
    return dict(rule='cache_only', scopes=scopes,
                cost=metric(missing('缺少实际缓存及同范围计价证据'), MISSING_CACHE),
                pass_at_1=metric(original.get('calculation', {}).get('pass_at_1', missing('严格 pass@1 未知')), original.get('pass_at_1_reason', '评测缺失')),
                scope='保持 original 任务、try、调用范围、输出、分母和价表，仅校正缓存。')


def incomplete_api_accounting(task_ids):
    report = corrected_v2([CaseUsage(case, llm_called=None, coverage_complete=False,
                                   issues=[MISSING_API]) for case in task_ids])
    report['cost'] = {'total_usd': None, 'complete': False, 'reasons': [MISSING_API]}
    return report


def replay(name, folder, destination):
    config = tomllib.loads((folder / name / 'experiment.toml').read_text())
    report = {'method': name, 'agent': config['agent'], 'model': config['requested_model'],
              'helper_model': config.get('helper_model_id'), 'requested_count': config['requested_count'], 'execution_status': config['execution_status'],
              'blockers': config['blockers'], 'scope': 'user-confirmed-original-task-population'}
    from .rq1_paper import published
    report['paper_reported'] = published(name)
    population = load_json(ROOT / config['task_source'])
    if config.get('task_selection') == 'first-n-in-source-order':
        if len(population) != config['population_count']:
            raise ValueError('Full Verified population size changed; review the frozen first-100 selection')
        population = population[:config['source_count']]
    task_ids = [d['instance_id'] if isinstance(d, dict) else d for d in population]
    if len(task_ids) != config['source_count'] or len(task_ids) != len(set(task_ids)):
        raise ValueError('Original task population count or uniqueness mismatch')
    saved_ids = folder / name / 'source-tasks.txt'
    if saved_ids.exists() and saved_ids.read_text().splitlines() != task_ids:
        raise ValueError('Frozen RQ1 task order changed; review before replacing the selection')
    report.update(selected=len(task_ids), task_ids=task_ids)
    if config.get('historical_trajectories') is False:
        report.update(original=None, cache_only=cache_correction(None, name),
                      corrected=incomplete_api_accounting(task_ids), pass_at_1=None,
                      archived_tasks=0, rows=[dict(task_id=case, original_counted=False,
                          source=config['task_source'] + f':record[{index}]',
                          input=None, output=None, cache=None, resolved=None)
                          for index, case in enumerate(task_ids)],
                      reason='Task list is fixed; original Claude Code trajectories and accounting are absent locally.')
        report['case_originals'] = {case: None for case in task_ids}
        return report
    reader = importlib.import_module(f'methods.{name}.historical').replay
    if 'archive' in config:
        documents = archive_documents(config, destination)
        check_population(documents, task_ids, config)
        original = reader(documents, native_cases(documents, config), config)
        case_originals = {case: reader({case: documents[case]}, native_cases({case: documents[case]}, config), config) for case in task_ids}
        rows = archive_rows(documents, config)
        original['diagnostics']['native_total_residual'] = sum(r['native_total_residual'] for r in rows)
        original['diagnostics']['residual_meaning'] = 'cost_tokens - prompt_tokens - completion_tokens; unclassified, never automatically added as reasoning/output'
        reference = paper_reference(config, original, documents, destination)
    else:
        original = reader(ROOT / config['source'], task_ids, config)
        rows = original.pop('rows')
        for row in rows:
            row['source'] = str(Path(row['source']).relative_to(ROOT))
        from methods.turn_control.historical import traced_original
        case_originals = {r['task_id']: traced_original([r], config) for r in rows}
        documents = {p.name: load_json(p) for p in (ROOT / config['source'] / 'log').glob('*.txt')}
        reference = None
    keys = usage_field_counts(documents)
    original['diagnostics']['raw_usage_fields_present'] = keys
    if keys:
        raise ValueError('Raw usage evidence was found; review coverage before reporting it unavailable')
    if name == 'turn_control' and 'gemini' in config.get('source', ''):
        records = [u['raw'] for row in rows for u in row.get('usage', [])]
        original['diagnostics']['gemini_last_try_usage'] = dict(
            requests=len(records), explicit_cache_fields=sum('cache_read_input_tokens' in u for u in records),
            positive_cache_requests=sum((u.get('cache_read_input_tokens') or 0) > 0 for u in records),
            known_cache_read_subtotal=sum(u.get('cache_read_input_tokens') or 0 for u in records),
            missing_cache_fields=sum('cache_read_input_tokens' not in u for u in records),
            interpretation='Original printed Gemini usage exists and proves cache hits; omitted cache fields and complete retry coverage are not certified.')
    report.update(rows=rows, original=original, archived_tasks=len(rows),
                  cache_only=cache_correction(original, name),
                  corrected=incomplete_api_accounting(task_ids), case_originals=case_originals,
                  archive_reference=reference, paper_reference=reference, pass_at_1=original['pass_at_1'])
    return report


def cache_detail_rows(reports):
    for report in reports:
        original = report['original'] or {}
        diagnostics = original.get('diagnostics', {})
        for scope, metrics in report['cache_only']['scopes'].items():
            helper = scope == 'method_auxiliary'
            assumed = diagnostics.get('helper_assumed_cache') if helper else original.get('cache')
            basis = ('492 tokens per helper analysis' if helper else
                     'common-prefix characters / 4' if report['method'] == 'attn_compress' else 'not reported')
            yield dict(method=report['method'], scope=scope,
                       original_cache_tokens=assumed, original_cache_basis=basis,
                       actual_cache_read_tokens=metrics['cache_read']['sum'],
                       actual_cache_write_tokens=metrics['cache_write']['sum'],
                       actual_cache_total_tokens=metrics['cache']['sum'],
                       actual_cache_read_known_subtotal=diagnostics.get('gemini_last_try_usage', {}).get('known_cache_read_subtotal'),
                       requests_missing_cache_fields=diagnostics.get('gemini_last_try_usage', {}).get('missing_cache_fields'),
                       original_pricing_assumed_cached_input=original.get('input')
                           if report['method'] == 'agent_diet' and not helper else None,
                       pricing_assumption_note='AgentDiet main price applies the cache rate to all input plus an extra 2% at normal rate; this is not a measured token split.'
                           if report['method'] == 'agent_diet' and not helper else None,
                       status='partial: printed Gemini cache hits exist; missing fields remain unknown'
                           if diagnostics.get('gemini_last_try_usage') else 'unknown: actual cache evidence absent')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, default=ROOT / 'RQ1')
    args = parser.parse_args()
    folder = args.study.resolve()
    manifest = tomllib.loads((folder / 'manifest.toml').read_text())
    with tempfile.TemporaryDirectory(prefix='tokenana-rq1-') as scratch:
        reports = []
        for name in manifest['experiments']:
            print(f'Replaying original archive: {name}', flush=True)
            reports.append(replay(name, folder, Path(scratch)))
    output = folder / 'reports'
    output.mkdir(parents=True, exist_ok=True)
    from .rq1_explain import explain
    from .accounting_trace import export_trace, merge_traces
    traces = [(r['method'], explain(r).payload()) for r in reports]
    trace = merge_traces(traces)
    rows = [row for row in trace['comparisons'] if row['case_id'] == '__all__']
    payload = {'schema_version': 2, 'selection_status': manifest['selection_status'],
               'formal_experiments_started': False, 'corrected_accounting': 'corrected-v2-api',
               'note': 'Original versus full v2, with a separate same-scope cache_only diagnostic.',
               'experiments': reports}
    export_trace(trace, output)
    write_text(output / 'results.json', json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    csv_file(output / 'metrics.csv', rows, legacy=True)
    csv_file(output / 'cache-details.csv', list(cache_detail_rows(reports)), legacy=True)
    csv_file(output / 'cases.csv', [dict(method=r['method'], **row) for r in reports for row in r['rows']], legacy=True)
    paper_rows = [dict(method=r['method'], metric=k, paper_reported=v['value'], source=v['source'], locator=v['locator'])
                  for r in reports for k, v in r['paper_reported']['calculation'].items()]
    csv_file(output / 'paper-values.csv', paper_rows, legacy=True)
    for report in reports:
        write_text(folder / report['method'] / 'source-tasks.txt', '\n'.join(report['task_ids']) + '\n')
    text = '# RQ1：original、仅缓存校正与完整 v2\n\n'
    text += '固定 600 题次；未启动模型或官方评测。original 保留作者规则，corrected 为完整 corrected-v2-api；cache_only 仅改变原范围的缓存处理。\n\n'
    text += '## 论文直接报告值\n\n' + markdown(paper_rows, ['method', 'metric', 'paper_reported']) + '\n\n'
    text += '论文印刷费用直接保留；下表为归档复算值，不覆盖论文值。新运行由各方法 run.sh 生成独立报告。\n\n'
    text += markdown(rows, ['method', 'scope', 'metric', 'original', 'cache_only', 'corrected_v2', 'cache_delta', 'total_delta'])
    text += '\n\n[逐题三方对照](accounting-comparison.csv) · [逐操作数步骤](accounting-steps.csv) · [完整计算依赖](accounting-trace.json)\n\n'
    text += 'Gemini turn_control 最后 try 的 2,573 条打印 usage 中，645 条明确报告缓存命中，共 11,910,948 token；原费用函数未应用缓存折扣。缺缓存字段和完整重试覆盖尚未认证，完整 v2 保持未知。其他归档仅有任务级汇总。估计缓存、假设扣除与实测缓存分栏。\n'
    text += '同一候选评测不因统计校正改变；turn_control 多次 try 的严格 pass@1 仍未知。原残差不自动解释为 reasoning。\n'
    write_text(output / 'summary.md', text)
    print(f'Report: {output / "summary.md"}')


if __name__ == '__main__':
    main()
