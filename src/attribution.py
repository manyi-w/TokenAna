"""Classify explicit native call metadata; absent evidence remains unknown."""
import json


def codex_attribution(request, headers):
    headers = {key.lower(): value for key, value in headers.items()}
    purpose = headers.get('x-tokenana-purpose')
    if purpose in ('main', 'agent_auxiliary'):
        return {'purpose': purpose}
    metadata = request.get('client_metadata') or {}
    try:
        turn = json.loads(headers.get('x-codex-turn-metadata', '{}'))
    except (ValueError, TypeError):
        turn = {}
    kind = turn.get('request_kind', metadata.get('request_kind'))
    subagent = headers.get('x-openai-subagent') or turn.get('subagent_kind') or metadata.get('subagent_kind')
    purpose = ('agent_auxiliary' if subagent or kind in ('memory', 'compaction', 'prewarm') else
               'main' if kind == 'turn' else 'unknown')
    return {'purpose': purpose}
