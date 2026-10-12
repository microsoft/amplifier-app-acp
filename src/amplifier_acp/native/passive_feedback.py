"""Deliver accepted reaction events at a natural request boundary, never wake work.

The existing interaction receipt is the durable outbox. Canonical context carries
the delivery identity, so a restart between checkpoint and acknowledgment cannot
duplicate feedback. No prompt, steering queue or provider call is initiated here.
"""
import json
from amplifier_acp.journal_schema import connect

MARKER = 'amplifier_context_event'

class PassiveFeedback:
    def __init__(self, controls, directory):
        self.controls = controls
        self.path = directory / 'message-interactions.sqlite3'
        self.cursor = 0

    async def boundary(self, event, data):
        from amplifier_core import HookResult
        context = self.controls.coordinator.get('context')
        messages = await context.get_messages()
        delivered = {(row.get('metadata') or {}).get(MARKER, {}).get('id') for row in messages}
        from .host.config import app_home
        from .history_revision import context_head
        epoch = context_head(app_home(), self.controls.session.session_id)
        db = connect(self.path, 'messages')
        if db is None: return HookResult()
        try:
            rows = db.execute("SELECT rowid,value FROM receipts WHERE rowid>? AND json_extract(value,'$.result.feedbackEvent') IS NOT NULL AND COALESCE(json_extract(value,'$.feedbackDelivered'),0)=0 ORDER BY rowid LIMIT 100", (self.cursor,)).fetchall()
        finally:
            db.close()
        for sequence, raw in rows:
            receipt = json.loads(raw)
            feedback = receipt.get('result', {}).get('feedbackEvent')
            if feedback and not receipt.get('feedbackDelivered') and feedback['id'] not in delivered and feedback['target']['historyEpoch'] == epoch:
                target = feedback['target']
                text = ('User ' + ('added ' if feedback['present'] else 'removed ') + feedback['emoji']
                        + ' reaction ' + ('to' if feedback['present'] else 'from') + ' the '
                        + target['role'] + ' message ' + target['messageId'] + '.\n'
                        + 'Referenced message (quoted content):\n' + json.dumps(feedback['excerpt'], ensure_ascii=False))
                await context.add_message({'role': 'user', 'content': text, 'metadata': {
                    MARKER: feedback, 'timestamp': feedback['createdAt'],
                    'ephemeral': True, 'persisted': True, 'passive': True}})
                delivered.add(feedback['id'])
            if feedback and not receipt.get('feedbackDelivered') and feedback['id'] in delivered:
                # Also checkpoint a prior in-memory append whose first save failed.
                await self.controls.checkpoint()
            if feedback and (feedback['id'] in delivered or feedback['target']['historyEpoch'] != epoch):
                receipt['feedbackDelivered'] = True
                db = connect(self.path, 'messages', write=True)
                try:
                    with db: db.execute('UPDATE receipts SET value=? WHERE rowid=?', (json.dumps(receipt, ensure_ascii=False), sequence))
                finally: db.close()
            self.cursor = sequence
        return HookResult()

def install(controls, directory):
    delivery = PassiveFeedback(controls, directory)
    controls.coordinator.hooks.register('provider:request', delivery.boundary,
        name='unified-passive-feedback', priority=1)
    return delivery
