"""Selected same-account reaction copy; no source mutation or replay authority."""
from . import history
from .message_metadata import MessageError, MessageMetadata, REACTIONS, digest, encoded
from .relocation import home

MAX_COPY_BYTES = 1024 * 1024


def prepare(server, source_id, target_id, rows, positions, context):
    """The lifecycle owner supplies canonical rows and its exact retained map.

    Read only the selected source adjunct, at most 50 IDs per query and a
    one MiB annotation plan within the existing 64 MiB lifecycle census.
    No global scan, display/text matching,
    foreign-account annotations, source receipts or quote reservations.
    """
    from .maintenance_gate import checked
    path=checked(home(server)/'sessions'/source_id/'message-interactions.sqlite3')
    if not path.exists():return []
    import sqlite3
    probe=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        if not probe.execute('SELECT 1 FROM annotations LIMIT 1').fetchone():return []
    finally:probe.close()
    principal=server.message_principal
    actor=principal(source_id) if principal else None
    target_actor=principal(target_id) if principal else None
    if not actor or actor != target_actor:
        raise MessageError('Reaction copy requires the same trusted account for source and target')
    store=MessageMetadata(home(server),source_id,actor_id=actor)
    db=store._db()
    plan=[];seen=set();plan_bytes=0
    try:
        db.execute('BEGIN')
        selected=[]
        for index,(offset,row) in enumerate(rows):
            target_index=positions.get(index)
            if target_index is None or not history.message_reference(source_id,row,offset,getattr(rows,'epoch',None)):
                continue
            identity=history.message_id(row,offset,getattr(rows,'epoch',None))
            selected.append((identity,target_index,row))
        for start in range(0,len(selected),50):
            batch=selected[start:start+50]
            ids=[item[0] for item in batch]
            placeholders=','.join('?' for _ in ids)
            values=db.execute('SELECT message,emoji FROM annotations WHERE actor=? AND message IN ('+placeholders+') ORDER BY message,emoji',(actor,*ids)).fetchall()
            by_id={}
            for identity,emoji in values:
                if emoji not in REACTIONS:raise MessageError('Unsupported retained reaction; original preserved')
                by_id.setdefault(identity,[]).append(emoji)
            for identity,target_index,row in batch:
                emoji=by_id.get(identity)
                if not emoji:continue
                if identity in seen:raise MessageError('Annotated canonical source identity is ambiguous')
                seen.add(identity)
                original=history.message_reference(source_id,row,0,None)
                retained=history.message_reference(target_id,context[target_index],0,None)
                if not retained or row['role']!=context[target_index]['role'] or original['sourceDigest']!=retained['sourceDigest']:
                    raise MessageError('Annotated public source row changed during copy transformation; original preserved')
                item={'sourceId':identity,'targetIndex':target_index,'rowDigest':digest(context[target_index]),'emoji':emoji,'actor':actor}
                plan_bytes+=len(encoded(item).encode())+1
                if plan_bytes>MAX_COPY_BYTES:raise MessageError('Reaction copy annotation plan exceeds one MiB; original preserved')
                plan.append(item)
        return plan
    finally:db.close()


def publish(server, target_id, target_rows, plan):
    """Verify persisted canonical target rows before atomic new annotations.

    Failure leaves the existing lifecycle marker/receipt unknown, never grants
    permission to repeat the copy. Untouched copies create no adjunct database.
    """
    if not plan:return {'policy':'same-account-retained-canonical','messages':0,'reactions':0,'receiptsCopied':False}
    actor=server.message_principal(target_id) if server.message_principal else None
    mapped=[];seen=set()
    for item in plan:
        if actor != item['actor']:raise MessageError('Trusted target account changed during reaction copy')
        index=item['targetIndex']
        if not 0<=index<len(target_rows):raise MessageError('Retained target row is unavailable')
        offset,row=target_rows[index]
        if digest(row)!=item['rowDigest'] or not history.message_reference(target_id,row,offset,getattr(target_rows,'epoch',None)):
            raise MessageError('Persisted retained target row differs from verified source')
        identity=history.message_id(row,offset,getattr(target_rows,'epoch',None))
        if identity in seen:raise MessageError('Retained target reaction identity is ambiguous')
        seen.add(identity);mapped.append((identity,item['emoji']))
    store=MessageMetadata(home(server),target_id,actor_id=actor)
    db=store._db(create=True)
    try:
        db.execute('BEGIN IMMEDIATE')
        if any(db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in ('annotations','revisions','receipts')):
            raise MessageError('Target message adjunct already contains authority; no overwrite')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='quotes'").fetchone():
            raise MessageError('Target quote reservations already exist; no overwrite')
        for identity,emoji in mapped:
            db.executemany('INSERT INTO annotations VALUES(?,?,?)',[(identity,actor,value) for value in emoji])
            db.execute('INSERT INTO revisions VALUES(?,1)',(identity,))
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK');raise
    finally:db.close()
    return {'policy':'same-account-retained-canonical','messages':len(mapped),'reactions':sum(len(values) for _,values in mapped),'receiptsCopied':False}
