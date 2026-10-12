"""Bind peer results to a saved native answer, never to a model declaration alone."""
import asyncio
import hashlib


CAPABILITY = {'version': 1, 'checkpointed': True, 'maxHistoryBytes': 67108864}


def text(row):
    value = row.get('content', '')
    return value if isinstance(value, str) else ''.join(
        block.get('text', '') for block in value
        if isinstance(block, dict) and block.get('type') in {'text', 'output_text'}
    ) if isinstance(value, list) else ''


def capture(directory, app_home, session_id, generation):
    from amplifier_acp import history
    from amplifier_acp.session_lifecycle import canonical
    ids = generation.get('input_ids', [])
    if (not isinstance(ids, list) or len(ids) > 200 or not generation.get('id')
            or not any(isinstance(i, str) and i.startswith('peer:') for i in ids)):
        return None
    path, diagnostics = history.source(directory)
    if diagnostics:
        return None
    rows, metadata, revision = canonical(directory, history.stamp(path)[0], app_home=app_home)
    if not rows or metadata.get('session_id') != session_id:
        return None
    position, last = rows[-1]
    answer = text(last)
    if last.get('role') != 'assistant' or last.get('tool_calls') or not answer.strip():
        return None
    # Only actual peer-origin inputs in the saved checkpoint can anchor a result.
    inputs = []
    for _, row in rows[:-1]:
        meta = row.get('metadata') or {}
        identity = (meta.get('amplifier_input') or {}).get('id')
        if identity in ids and meta.get('inputOrigin') == 'peer':
            inputs.append(identity)
    if not inputs or len(inputs) != len(set(inputs)):
        return None
    return {'version': 1, 'rootSessionId': session_id, 'generationId': generation['id'],
            'inputIds': inputs, 'messageId': history.message_id(last, position, rows.epoch),
            'sourceRevision': revision, 'textDigest': hashlib.sha256(answer.encode()).hexdigest()}


def install(worker):
    coordinator = worker.session.coordinator
    checkpoint = coordinator.get_capability('live.checkpoint')
    if checkpoint is None or coordinator.get_capability('native.peer-results'):
        return

    async def checked(*args, **kwargs):
        worker.peer_terminal = None
        result = await checkpoint(*args, **kwargs)
        generation = worker.runtime.generation or {}
        if any(isinstance(i, str) and i.startswith('peer:') for i in generation.get('input_ids', [])):
            try:
                worker.peer_terminal = await asyncio.to_thread(capture, worker.naming.directory,
                    worker.home, worker.runtime.session_id, dict(generation))
            except (ValueError, OSError, TypeError):
                # Missing/bounded/malformed history is absence of qualification,
                # never a reason to repeat work or fail a completed conversation.
                pass
        return result

    coordinator.register_capability('live.checkpoint', checked)
    coordinator.register_capability('native.peer-results', CAPABILITY)


def terminal(worker, event):
    proof = getattr(worker, 'peer_terminal', None)
    if (event.get('type') == 'generation.finished' and isinstance(proof, dict)
            and proof['generationId'] == event.get('generation_id')
            and event.get('disposition') == 'manager_turn_finished'
            and event.get('active_job_ids') == [] and isinstance(event.get('text'), str)
            and proof['textDigest'] == hashlib.sha256(event['text'].encode()).hexdigest()
            and all(i in event.get('input_ids', []) for i in proof['inputIds'])):
        return proof
    return None
