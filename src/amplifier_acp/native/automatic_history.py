"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import hashlib


import json


from pathlib import Path


from .session_files import amplifier_home, project_slug


from .shared_state_probe import text_content


def directory(session):
    project = session['nativeProject']
    if not isinstance(project, str) or project in {'', '.', '..'} or Path(project).name != project:
        raise ValueError('Invalid native project identity.')
    root = amplifier_home() / 'projects'
    session_id = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    if not isinstance(session_id, str) or session_id in {'', '.', '..'} or '/' in session_id or '\\' in session_id or '\x00' in session_id:
        raise ValueError('Invalid native session identity.')
    path = root / project / 'sessions' / session_id
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Session storage must remain inside the shared projects folder.')
    return path


def revision(session):
    for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
        try:
            info = (directory(session) / name).stat()
            return [info.st_mtime_ns, info.st_size]
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def display_identity(session, index, role, text):
    key = json.dumps([session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id'], index, role, text], ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def display_message(row, index, session, *, include_internal=False):
    if not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'}:
        return None
    if not include_internal and (row.get('metadata') or {}).get('ephemeral'):
        return None
    from amplifier_acp.public_message import row_envelope
    try:original=row_envelope(row)
    except ValueError:return None
    text = ''.join(block['text'] for block in original['blocks'] if block['type']=='text') if original is not None else text_content(row)
    if not text and original is None:
        return None
    from .session_store import message_time
    metadata=row.get('metadata') or {}
    provenance=metadata.get('amplifier_input',{})
    recovery=metadata.get('live_recovery_job')
    if 'amplifier_input' not in metadata and isinstance(recovery,str) and 0<len(recovery)<=128:
        # Earlier loop-live versions recorded recovery ownership separately.
        # Read that host metadata; matching text alone is never provenance.
        provenance={'version':1,'kind':'service','id':recovery,'source':'local-job-recovery'}
    observation={}
    input_identity = {}
    if (row['role'] == 'user' and isinstance(provenance, dict)
            and provenance.get('version') == 1 and provenance.get('kind') == 'user'
            and isinstance(provenance.get('id'), str) and 0 < len(provenance['id']) <= 200):
        input_identity = {'nativeInputId': provenance['id']}
    if isinstance(provenance,dict) and provenance.get('version')==1 and provenance.get('kind')=='service' and all(isinstance(provenance.get(key),str) and 0<len(provenance[key])<=128 for key in ('id','source')):
        observation={'observation':{key:provenance[key] for key in ('id','source','call_id') if key in provenance}}
        from .recovery_observations import recovery_facts
        facts = recovery_facts(metadata, text, provenance)
        if facts:
            observation['observation']['recovery'] = facts
    quote = metadata.get('replyTo')
    reply = {'replyTo': quote} if isinstance(quote, dict) and len(json.dumps(quote).encode()) <= 32768 else {}
    return {'id': display_identity(session, index, row['role'], text), 'role': row['role'],
            'text': text, 'via': 'chat', 'source': 'native', 'nativeIndex': index,
            'createdAt': message_time(row) or session.get('createdAt', 0), 'timestampKnown': message_time(row) is not None,
            **input_identity, **observation, **reply, **({'publicMessage':original} if original is not None else {})}
