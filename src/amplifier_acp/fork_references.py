"""Exact selected output references across an already-authorized native copy.

This carries identities only. Resources owns output bytes; no input is replayed.
"""
import hashlib
import json
import re
from . import history


def preserved_rows(directory, app_home, cwd, expected_revision):
    """Verify archived originals without giving them current copy authority."""
    from .session_lifecycle import HistoryRows, regular, MAX_HISTORY
    from .native.history_revision import context_head
    head, segments, guards = history._exact_segments(directory, app_home, cwd)
    result = []
    remaining = MAX_HISTORY
    for location, epoch, descriptor in segments[1:]:
        path = location / 'transcript.jsonl'
        before = history.stamp(path)[0]
        raw = regular(path, remaining)
        remaining -= len(raw)
        if hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise ValueError('Preserved output source bytes changed')
        rows = HistoryRows(epoch=epoch)
        offset = 0
        for line in raw.splitlines(keepends=True):
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('role'), str):
                raise ValueError('Preserved output source is malformed')
            rows.append((offset, row))
            offset += len(line)
        result.append(rows)
        guards.append((path, before))
    if (history.stamp(directory / 'transcript.jsonl')[0] != expected_revision
            or context_head(app_home, directory.name, cwd) != head
            or any(history.stamp(path)[0] != stamp for path, stamp in guards)):
        raise ValueError('Output source history changed during copy admission')
    return result


def prepare(references, rows, selected, positions, *, source_session=None, preserved=()):
    if references is None:
        return None
    if not isinstance(references, list) or len(references) > 500 or len(json.dumps(references).encode()) > 131072:
        raise ValueError('Fork output references exceed their complete selection bound')
    keys = set()
    indexed = []
    for segment in (rows, *preserved):
        current = None
        for ordinal, (position, row) in enumerate(segment):
            marker = (row.get('metadata') or {}).get('amplifier_input') or {}
            if row.get('role') == 'user' and history.visible(row):
                current = marker.get('id') or history.message_id(row, position, getattr(segment, 'epoch', None))
            updates, diagnostics = history.content_updates(row, position)
            text = ''.join(update.get('content', {}).get('text', '') for update in updates
                           if update.get('sessionUpdate') in {'user_message_chunk', 'agent_message_chunk'})
            # Main's display identity binds the native session, original ordinal,
            # role, and raw text. It is never a match on similar text alone.
            content = row.get('content', '')
            raw_text = content if isinstance(content, str) else '\n'.join(
                block.get('text', '') for block in content if isinstance(block, dict)
                and block.get('type') in {'text', 'output_text'}) if isinstance(content, list) else ''
            legacy_id = hashlib.sha256(json.dumps([source_session, ordinal, row.get('role'), raw_text],
                ensure_ascii=False).encode()).hexdigest()[:32] if source_session and getattr(segment, 'epoch', None) is None else None
            indexed.append((position, row, current, text, diagnostics, legacy_id, history.message_id(row, position, getattr(segment, 'epoch', None)), segment is rows))
    selected_positions = {position: index for index, (position, _) in enumerate(selected)}
    plan = []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) not in ({'key', 'messageId'}, {'key', 'legacyMessageId'}, {'key', 'inputId', 'role', 'textSha256'}):
            raise ValueError('Exact original output reference required')
        if any(not isinstance(value, str) or not value or len(value) > 512 for value in reference.values()) or reference['key'] in keys:
            raise ValueError('Invalid or duplicate output reference')
        keys.add(reference['key'])
        if 'inputId' in reference and (reference['role'] not in {'user', 'assistant'} or not re.fullmatch('[a-f0-9]{64}', reference['textSha256'])):
            raise ValueError('Invalid admitted output message witness')
        matches = []
        for position, row, input_id, text, diagnostics, legacy_id, identity, active in indexed:
            if diagnostics or not history.visible(row) or row.get('role') not in {'user', 'assistant'} or not text.strip():
                continue
            marker = (row.get('metadata') or {}).get('amplifier_input') or {}
            matched = (reference['legacyMessageId'] == legacy_id if 'legacyMessageId' in reference else
                       reference['messageId'] in (identity, marker.get('id')) if 'messageId' in reference else
                       input_id == reference['inputId'] and row['role'] == reference['role'] and hashlib.sha256(text.encode()).hexdigest() == reference['textSha256'])
            if matched:
                matches.append((position, identity, active))
        if len(matches) != 1:
            raise ValueError('Original output message is unavailable or ambiguous; copy was not started')
        position, identity, active = matches[0]
        selected_index = selected_positions.get(position) if active else None
        target_index = positions.get(selected_index) if selected_index is not None else None
        plan.append({'key': reference['key'], 'sourceMessageId': identity, 'targetIndex': target_index})
    return plan


def publish(plan, target_rows, context, preserve_system):
    if plan is None:
        return None
    result = []
    for item in plan:
        value = {key: item[key] for key in ('key', 'sourceMessageId')}
        index = item['targetIndex']
        if index is None:
            value['disposition'] = 'omitted'
        else:
            index = sum(1 for row in context[:index] if preserve_system or row.get('role') not in {'system', 'developer'})
            position, row = target_rows[index]
            value.update(disposition='retained', targetMessageId=history.message_id(row, position, getattr(target_rows, 'epoch', None)))
        result.append(value)
    return {'version': 1, 'items': result, 'complete': True, 'execution': False}
