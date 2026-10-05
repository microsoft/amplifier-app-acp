"""Typed public originals on canonical Native rows; never execution or access authority."""
import copy
import json
import re

META = 'amplifier.dev/publicMessage'
ENVELOPE = 'amplifier_public_message'
COPY_SOURCE = 'amplifier_public_copy_source'
HISTORY_ONLY = 'amplifier_public_reference_only'
CAPABILITY = {'version': 1, 'routes': ['prompt', 'steer.submit', 'history.revise'],
              'variants': ['text', 'resources', 'native-attachments'],
              'publicRows': True, 'exactRowRead': True, 'runtimeNegotiated': True}


def validate(value, input_id):
    if (not isinstance(value, dict) or set(value) != {'version', 'inputId', 'capture', 'blocks'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(input_id, str) or not 1 <= len(input_id) <= 128
            or value['inputId'] != input_id
            or not isinstance(value['capture'], str) or value['capture'] not in {'host-standard', 'native-input'}
            or not isinstance(value['blocks'], list)):
        raise ValueError('Exact v1 public message and admitted input identity required')
    for block in value['blocks']:
        if not isinstance(block, dict):
            raise ValueError('Public message blocks must be typed objects')
        if block.get('type') == 'text':
            if set(block) != {'type', 'text'} or not isinstance(block['text'], str):
                raise ValueError('Literal public text required')
            continue
        required = {'type', 'reference', 'name', 'mimeType'}
        if block.get('type') != 'attachment' or not required <= set(block) or set(block) - required - {'size', 'sha256'}:
            raise ValueError('Public cards contain descriptors only')
        reference = block['reference']
        if (not isinstance(reference, dict) or set(reference) != {'owner', 'id'}
                or not isinstance(reference['owner'], str) or reference['owner'] not in {'resources', 'native-attachments'}
                or not isinstance(reference['id'], str) or not reference['id']):
            raise ValueError('Opaque attachment owner reference required')
        name_limit = 255 if reference['owner'] == 'resources' else 200
        if (not isinstance(block['name'], str) or len(block['name']) > name_limit
                or not isinstance(block['mimeType'], str) or not block['mimeType']):
            raise ValueError('Public card name or MIME type is invalid')
        if 'size' in block and (type(block['size']) is not int or block['size'] < 0):
            raise ValueError('Public card size must be a nonnegative integer')
        if 'sha256' in block and (not isinstance(block['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', block['sha256'])):
            raise ValueError('Public card digest must be SHA-256')
    # JSON validation also rejects non-finite/non-transport values. No new quota.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def row_envelope(row):
    metadata = row.get('metadata') or {}
    if ENVELOPE not in metadata:
        return None
    marker = metadata.get('amplifier_input') or {}
    if (row.get('role') != 'user' or marker.get('kind', 'user') not in {'user', 'steer'}
            or marker.get('source', 'user') != 'user'):
        raise ValueError('Public original is not bound to a public admitted user row')
    return validate(metadata[ENVELOPE], marker.get('id'))


def validate_locator(value):
    if (not isinstance(value, dict) or set(value) != {'sessionId', 'messageId', 'position', 'historyEpoch'}
            or not isinstance(value['sessionId'], str) or not value['sessionId']
            or not isinstance(value['messageId'], str) or not value['messageId'].startswith('native:')
            or type(value['position']) is not int or value['position'] < 0
            or value['historyEpoch'] is not None and not isinstance(value['historyEpoch'], str)):
        raise ValueError('Exact persisted Native row locator required')
    from .message_metadata import token
    token(value['sessionId'])
    token(value['messageId'])
    epoch=value['historyEpoch']
    if epoch is not None:token(epoch)
    prefix='native:'+(epoch+':' if epoch else '')
    if not re.fullmatch(re.escape(prefix)+'[a-f0-9]{24}',value['messageId']):
        raise ValueError('Native row identity must bind its selected history epoch')
    return copy.deepcopy(value)


def validate_copy(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'source', 'disposition'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['disposition'], str) or value['disposition'] not in {'retained', 'history-only'}):
        raise ValueError('Exact immediate-source Native copy fact required')
    return {**value, 'source': validate_locator(value['source'])}


def locator(session_id, row, position, epoch):
    from .history import message_id
    return {'sessionId': session_id, 'messageId': message_id(row, position, epoch),
            'position': position, 'historyEpoch': epoch}


def public_row(session_id, row, position, epoch):
    from .history import visible
    if not visible(row):
        return None
    envelope = row_envelope(row)
    if envelope is None:
        return None
    result = {'locator': locator(session_id, row, position, epoch), 'publicMessage': envelope}
    metadata = row.get('metadata') or {}
    if COPY_SOURCE in metadata:
        result['copySource'] = validate_copy(metadata[COPY_SOURCE])
    return result


def transport(server, meta, input_id):
    # Absence/old negotiation keeps ordinary ACP behavior and Host originals.
    NATIVE = 'amplifier.dev/native'
    if (NATIVE not in server.extensions
            or not isinstance(meta, dict) or META not in meta):
        return None
    return validate(meta[META], input_id)


def capture_native_input(input_id, text, attachments=()):
    """Direct Native input seam, before mentions/quotes/model attachment encoding."""
    blocks = [{'type': 'text', 'text': text}]
    blocks.extend({'type': 'attachment', 'reference': {'owner': 'native-attachments', 'id': item['id']},
                   'name': item['name'], 'mimeType': item['mime'], 'size': item['size']} for item in attachments)
    return validate({'version': 1, 'inputId': input_id, 'capture': 'native-input', 'blocks': blocks}, input_id)


class PendingOriginals:
    """Transient queued-input handoff; admission/backpressure belong to Runtime."""
    def __init__(self):
        self.values = {}

    def stage(self, input_id, value):
        if value is None:
            return
        value = validate(value, input_id)
        if input_id in self.values:
            raise ValueError('Public original input is already staged; no replay')
        self.values[input_id] = json.dumps(value, ensure_ascii=False, allow_nan=False)

    def get(self, input_id):
        value = self.values.get(input_id)
        return json.loads(value) if value is not None else None

    def discard(self, input_id):
        self.values.pop(input_id, None)


def read_row(directory, selected, *, epoch=None, max_bytes=2_000_000, expected_revision=None):
    from pathlib import Path
    from .history import source, stamp, HistoryError, message_id
    from .maintenance_gate import checked
    directory=checked(directory)
    selected=validate_locator(selected)
    if selected['sessionId'] != Path(directory).name or selected['historyEpoch'] != epoch:
        raise HistoryError('Native row belongs to another session or history epoch')
    path,diagnostics=source(directory)
    if diagnostics:
        raise HistoryError('Primary Native row history is unavailable')
    checked(path)
    revision,size=stamp(path)
    if expected_revision is not None and expected_revision != revision:
        raise HistoryError('Native history changed since inspection')
    position=selected['position']
    if position >= size:
        raise HistoryError('Native row locator is outside retained history')
    with path.open('rb') as stream:
        if position:
            stream.seek(position-1)
            if stream.read(1) != b'\n':
                raise HistoryError('Native row locator is not a record boundary')
        stream.seek(position)
        raw=stream.readline(max_bytes+1)
    if len(raw)>max_bytes or not raw.endswith(b'\n'):
        raise HistoryError('Native row is incomplete or oversized for the existing history read')
    if stamp(path)[0] != revision:
        raise HistoryError('Native history changed during row read')
    row=json.loads(raw)
    if not isinstance(row,dict) or message_id(row,position,epoch) != selected['messageId']:
        raise HistoryError('Native persisted row identity changed')
    original=public_row(Path(directory).name,row,position,epoch)
    return {'revision':revision,'publicRows':[original] if original is not None else [],
            'publicRowsComplete':original is not None,'selection':'row','nextCursor':None,
            'diagnostics':[] if original is not None else [{'code':'public_original_unavailable'}]}


def supports_context(context):
    """Use the loaded shared-library boundary, never a Native provider filter."""
    import sys
    try:
        from amplifier_module_context_simple.request_view import request_view
    except ImportError:
        return False
    supported={'amplifier_module_context_simple','amplifier_module_context_managed',
               'amplifier_module_context_managed.boundary'}
    if not callable(getattr(context,'get_messages_for_request',None)):
        return False
    for name in ('get_messages_for_request','get_messages_for_request_retaining','get_measured_request_view'):
        method=getattr(context,name,None)
        if not callable(method):
            continue
        owner=getattr(method,'__module__',None)
        if owner not in supported or getattr(sys.modules.get(owner),'request_view',None) is not request_view:
            return False
    return True


def mounted_capability(context):
    return ({**CAPABILITY, 'supported': True} if supports_context(context) else
            {'version': 1, 'supported': False, 'originalAuthority': 'host',
             'reason': 'shared-request-view-unavailable'})


def input_original(data):
    if 'public_message' in data:
        return validate(data['public_message'], data['input_id'])
    # The ACP adapter explicitly marks already converted transport. An absent
    # Host envelope must never be reconstructed from its expanded prompt.
    if data.get('public_input_transport') == 'acp':
        return None
    if 'public_input_transport' in data:
        raise ValueError('Unknown public input transport')
    return capture_native_input(data['input_id'], data['text'], data.get('attachments', ()))
