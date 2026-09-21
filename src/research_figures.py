"""Offline publication figures. Each resource comparison stays inside its cell."""
from collections import Counter, defaultdict
from pathlib import Path
import re


def render(output, report, content, context, bridges):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return {'status': 'unavailable', 'reason': 'Prepared analysis environment needs requirements/analysis.txt; tables remain available.'}
    directory = Path(output) / 'figures'
    directory.mkdir()
    paths = []
    plt.rcParams.update({'font.size': 9, 'svg.fonttype': 'none', 'pdf.fonttype': 42})
    def save(fig, name):
        name = re.sub(r'[^A-Za-z0-9._-]+', '-', name)
        fig.tight_layout()
        for extension in ('pdf', 'svg'):
            path = directory / f'{name}.{extension}'
            fig.savefig(path, bbox_inches='tight', metadata={'Creator': 'TokenAna'})
            paths.append(str(path.relative_to(output)))
        plt.close(fig)
    def empty(ax, title):
        ax.set_title(title)
        ax.text(.5, .5, 'Insufficient complete evidence', ha='center', va='center', transform=ax.transAxes)
    units = defaultdict(list)
    for row in report['aggregates']:
        units[(row['group'], row['agent'], row['model'])].append(row)
    for unit, entries in units.items():
        label = '/'.join(unit)
        if entries[0]['dataset'] == 'deepswe':
            fig, axes = plt.subplots(1, 3, figsize=(13, 4))
            for ax, metric in zip(axes, ('tokens', 'cost_usd', 'seconds')):
                complete = sorted([r for r in entries if r[metric] is not None and r.get('comparable', True)], key=lambda r: r[metric])
                if not complete:
                    empty(ax, metric)
                    continue
                ax.barh([r['method'] for r in complete], [r[metric] for r in complete], color='#287B8E')
                ax.invert_yaxis()
                ax.set_xlabel(metric)
                ax.set_title('Complete configurations only')
            fig.suptitle(label)
            save(fig, 'ranking-' + label)
        fig, ax = plt.subplots(figsize=(7, 4))
        complete = [r for r in entries if r['tokens'] is not None and r['original_tokens'] is not None]
        if complete:
            x = list(range(len(complete)))
            ax.bar([i-.2 for i in x], [r['original_tokens'] for r in complete], .4, label='Original')
            ax.bar([i+.2 for i in x], [r['tokens'] for r in complete], .4, label='Corrected v2 API')
            ax.set_xticks(x, [r['method'] for r in complete], rotation=25)
            ax.legend()
            ax.set_ylabel('Token total (see policy denominators in CSV)')
        else:
            empty(ax, label)
        save(fig, 'policies-' + label)
    for name, data in report['pareto'].items():
        fig, ax = plt.subplots(figsize=(7, 4))
        if data['points']:
            for p in data['points']:
                ax.scatter(p['resource'], p['resolved'], marker='D' if p['id'] in data['frontier'] else 'o')
                ax.annotate(p['id'].split('__')[1], (p['resource'], p['resolved']), xytext=(5, 4), textcoords='offset points')
            ax.set_xlabel(name.rsplit('/', 1)[-1])
            ax.set_ylabel('Resolved / selected tasks')
            ax.set_ylim(0, 1.05)
            ax.set_title(name + '\nDiamond = Pareto frontier')
        else:
            empty(ax, name)
        save(fig, 'pareto-' + name)
    categories = Counter()
    for row in content:
        categories[row['category']] += row['characters']
    fig, ax = plt.subplots(figsize=(8, 4))
    if categories:
        ax.barh(list(categories), list(categories.values()))
        ax.set_xlabel('Visible characters sent, including repeated history (not tokens)')
    else:
        empty(ax, 'Content structure')
    save(fig, 'content-structure')
    contexts = defaultdict(list)
    for row in context:
        contexts[(row['configuration_id'], row['case_id'])].append(row)
    # Deterministic examples are labeled as cases, not aggregate causal effects.
    for (config, case), values in list(sorted(contexts.items()))[:12]:
        fig, ax = plt.subplots(figsize=(8, 4))
        values.sort(key=lambda r: r['request_order'])
        ax.plot([r['request_order'] for r in values], [r['visible_characters'] for r in values], label='Visible request')
        ax.plot([r['request_order'] for r in values], [r['repeated_characters'] for r in values], label='Repeated content')
        ax.legend()
        ax.set(xlabel='Recorded request order', ylabel='Characters, not tokens', title=f'{config}\n{case}')
        save(fig, 'context-' + config + '-' + case)
    fig, ax = plt.subplots(figsize=(9, 4))
    complete_bridges = [r for r in report['diagnostics']['policy_bridge'] if r['metric'] == 'total' and r['complete']]
    if complete_bridges:
        config = complete_bridges[0]['configuration_id']
        values = [r for r in report['diagnostics']['policy_bridge'] if r['metric'] == 'total' and r['configuration_id'] == config]
        ax.plot([r['stage'] for r in values], [r['sum'] if r['sum'] is not None else float('nan') for r in values], marker='o')
        ax.set_title(config + '\nGaps mean unidentified policy bridges')
        ax.tick_params(axis='x', rotation=20)
    else:
        empty(ax, 'Ordered policy bridge')
    save(fig, 'policy-bridge')
    fig, ax = plt.subplots(figsize=(10, 4))
    timing = report['diagnostics'].get('time', [])
    values = [r for r in timing if r.get('seconds') is not None][:20]
    if values:
        ax.barh([r['phase'] + '/' + r['case_id'] for r in values], [r['seconds'] for r in values])
        ax.set_xlabel('Seconds (nested phases overlap; do not sum)')
    else:
        empty(ax, 'Time phases')
    save(fig, 'time-phases')
    return {'status': 'generated', 'files': paths, 'context_case_selection': 'First 12 available cases in fixed matrix order',
            'note': 'Empty panels identify missing evidence; incomplete configurations are not plotted as zero.'}
