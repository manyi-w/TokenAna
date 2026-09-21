"""Shared artifact handling; native completion and control rules stay in adapters."""
import json

from .patches import capture_patch


def collect_agent_patch(workspace, artifacts, process, options, errors):
    """Capture only after the transport returned; keep failure evidence explicit."""
    patch = ''
    if process is not None:
        try:
            patch = capture_patch(workspace, artifacts, timeout=options.get('diff_timeout', 60))
        except Exception as error:
            errors.append(f'patch capture failed: {error}')
    (artifacts.host / 'patch.diff').write_text(patch, encoding='utf-8')
    return patch


def check_session_outcome(directory, errors, *,
                          incomplete='Incomplete or terminated method session',
                          missing='Missing method session outcome',
                          invalid_errors=(OSError, ValueError)):
    try:
        outcome = json.loads((directory / 'session-outcome.json').read_text())
        if not outcome.get('complete') or outcome.get('termination'):
            errors.append(incomplete)
    except invalid_errors:
        errors.append(missing)


def save_patch_eligibility(directory, patch, eligible, *, diagnostic_always=True):
    if diagnostic_always or not eligible:
        (directory / 'diagnostic.diff').write_text(patch, encoding='utf-8')
    if not eligible:
        (directory / 'patch.diff').write_text('', encoding='utf-8')
