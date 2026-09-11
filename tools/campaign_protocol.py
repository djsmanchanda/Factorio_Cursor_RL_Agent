# Path: tools/campaign_protocol.py | Purpose: Decode campaign model evidence and verdicts consistently.
"""Transport parsing shared by the primary controller and aspect observers."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable


def _events(output: str) -> list[Any]:
    events = []
    for line in output.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _texts(events: list[Any]) -> str:
    texts = []
    for event in events:
        if isinstance(event, dict) and event.get('type') == 'text':
            part = event.get('part', {})
            if isinstance(part, dict) and isinstance(part.get('text'), str):
                texts.append(part['text'])
    return '\n\n'.join(texts)


def assistant_text(output: str) -> str:
    return _texts(_events(output))


def session_ids(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'sessionID', 'session_id'} and isinstance(child, str) and child:
                yield child
            yield from session_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from session_ids(child)


def session_id(output: str) -> str | None:
    for event in _events(output):
        found = next(session_ids(event), None)
        if found:
            return found
    return None


def decision_fields(output: str, marker: str = 'CAMPAIGN_DECISION') -> dict[str, str]:
    events = _events(output)
    # Plain verdicts support offline callers. Once transport events exist,
    # stderr/tool text cannot stand in for a missing assistant response.
    text = _texts(events) if events else output
    statuses = {'change', 'no-change', 'stop'} if marker == 'CAMPAIGN_DECISION' else {'approved', 'rejected'}
    result = {}
    for chunk in re.split(r'(?m)^' + re.escape(marker) + r':\s*$', text)[1:]:
        fields = dict(line.split(':', 1) for line in chunk.splitlines() if ':' in line)
        fields = {key.strip(): value.strip() for key, value in fields.items()}
        if fields.get('status') in statuses:
            result = fields
    return result
