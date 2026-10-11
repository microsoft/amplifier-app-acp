"""Passive, bounded plan progress from canonical accepted tool receipts."""
import json
from collections import deque
from pathlib import Path

from . import history
from .plan import history_plans

CAPABILITY = {'version': 1, 'passive': True, 'maxHistoryBytes': 67108864, 'historyPoints': 16}


def overview(directory, include_items=False):
    if type(include_items) is not bool:
        raise ValueError('includeItems must be boolean')
    directory = Path(directory)
    inspected = history.inspect(directory)
    if not inspected['available']:
        return {'plan': None, 'historyRevision': inspected['revision']}
    path, diagnostics = history.source(directory)
    revision, size = history.stamp(path)
    if size > CAPABILITY['maxHistoryBytes']:
        raise ValueError('Plan history exceeds the bounded read')
    calls = {}
    points = deque(maxlen=16)
    latest = None
    current = None
    consumed = 0
    with path.open('rb') as stream:
        while True:
            line = stream.readline(1048577)
            if not line:
                break
            position = consumed
            consumed += len(line)
            if len(line) > 1048576 or consumed > CAPABILITY['maxHistoryBytes']:
                raise ValueError('Plan history record exceeds the bounded read')
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('role'), str):
                raise ValueError('Plan history has an invalid record')
            if history.starts_turn(row, current):
                calls.clear()
                current = (row.get('metadata') or {}).get('amplifier_input', {}).get('id') or history.message_id(row, position)
            values, _ = history.content_updates(row, position)
            for plan in history_plans(values, calls):
                unavailable = plan.get('_meta', {}).get('amplifier.dev/todo', {}).get('unavailable') is True
                entries = plan['entries']
                latest = {'unavailable': unavailable, 'done': sum(e['status'] == 'completed' for e in entries),
                          'total': len(entries), 'turnId': current}
                if include_items and not unavailable:
                    latest['items'] = [{'content': e['content'], 'status': e['status']} for e in entries]
                if not unavailable:
                    # No inferred timestamp, interpolation, or percentage smoothing.
                    at = plan.get('_meta', {}).get('amplifier.dev/history', {}).get('createdAt')
                    points.append({'done': latest['done'], 'total': latest['total'],
                                   **({'at': at} if at is not None else {})})
    if history.stamp(path)[0] != revision or consumed != size:
        raise ValueError('Plan history changed during read')
    if latest is not None:
        latest['history'] = list(points)
        latest['source'] = 'accepted-native-todo'
    return {'plan': latest, 'historyRevision': revision, 'diagnostics': diagnostics}
