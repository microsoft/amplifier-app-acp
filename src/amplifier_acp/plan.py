"""Bounded ACP Plan snapshots from accepted root-session todo receipts.

Plans are presentation only: never add a message or trigger execution. The
optional todo metadata retains active wording and marks an unreadable latest
receipt; generic ACP clients still receive a standard plan (empty on failure).
"""
import json


def _object(value):
    if isinstance(value, str):
        if len(value) > 1_000_000:
            raise ValueError('Oversized todo receipt')
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Invalid todo receipt')
    return value


def accepted_plan(arguments, result):
    """None means explicitly rejected. Malformed accepted results clear stale plans."""
    try:
        output = _object(result)
        if output.get('success') is False:
            return None
        output = _object(output.get('output', output))
        if output.get('success') is False:
            return None
        todos = output.get('todos')
        if todos is None and output.get('status') in {'created', 'updated'}:
            args = _object(arguments)
            if args.get('action') in {'create', 'update'}:
                todos = args.get('todos')
        if not isinstance(todos, list) or len(todos) > 200:
            raise ValueError('Missing or oversized plan')
        entries = []
        size = 0
        for item in todos:
            if not isinstance(item, dict) or item.get('status') not in {'pending', 'in_progress', 'completed'}:
                raise ValueError('Invalid plan item')
            text, active = item.get('content'), item.get('activeForm', '')
            if not isinstance(text, str) or not isinstance(active, str) or max(len(text), len(active)) > 4000:
                raise ValueError('Invalid plan text')
            size += len(text) + len(active)
            if size > 16000:
                raise ValueError('Oversized plan')
            entries.append({'content': text, 'status': item['status'], 'priority': 'medium',
                            **({'_meta': {'amplifier.dev/todo': {'activeForm': active}}} if active else {})})
        return {'sessionUpdate': 'plan', 'entries': entries}
    except (ValueError, TypeError, KeyError):
        return {'sessionUpdate': 'plan', 'entries': [], '_meta': {'amplifier.dev/todo': {'unavailable': True}}}


def history_plans(updates, calls):
    """Pair canonical calls/results within a complete history turn, without I/O."""
    snapshots = []
    for update in updates:
        identity = update.get('toolCallId')
        if update.get('sessionUpdate') == 'tool_call':
            if update.get('title') == 'todo':
                calls[identity] = update.get('rawInput')
        elif update.get('sessionUpdate') == 'tool_call_update' and identity in calls:
            arguments = calls.pop(identity)
            if update.get('status') != 'completed':
                continue
            snapshot = accepted_plan(arguments, update.get('rawOutput'))
            if snapshot is not None:
                snapshot['_meta'] = {**update.get('_meta', {}), **snapshot.get('_meta', {})}
                snapshots.append(snapshot)
    return snapshots


def proposed_arguments(arguments):
    """Retain only bounded todo text until its success receipt arrives."""
    plan = accepted_plan(arguments, {'status': 'created'})
    if not plan or plan.get('_meta'):
        return None
    return {'action': 'update', 'todos': [
        {'content': item['content'], 'status': item['status'],
         **({'activeForm': item['_meta']['amplifier.dev/todo']['activeForm']} if item.get('_meta') else {})}
        for item in plan['entries']]}
