"""Presentation of a frozen canonical export plus bounded owner observations.

Contributions are export data, never canonical history or execution authority.
"""
import copy
import datetime
import json
import math
import re

FLAGS = ('includeExecution', 'includeConversationId', 'includeTimestamps', 'includeReferenceIds')
CAPABILITY = {'version': 1, 'flags': list(FLAGS), 'contextVersion': 1,
              'maxContextBytes': 1048576, 'maxWorkItems': 1000, 'maxArtifacts': 1000}


def options(args):
    for key in ('minimal', *FLAGS):
        if key in args and type(args[key]) is not bool:
            raise ValueError('Export options must be booleans')
    return {key: args.get(key, not args.get('minimal',False) if key=='includeConversationId' else key=='includeReferenceIds') for key in FLAGS}


def contribution(value):
    if value is None:
        return {'work': [], 'artifacts': [], 'omissions': ['owner_observations_not_supplied']}
    if (not isinstance(value, dict) or set(value) != {'version', 'source', 'work', 'artifacts', 'omissions'}
            or type(value['version']) is not int or value['version'] != 1
            or value['source'] != 'host-retained-observations'
            or len(json.dumps(value, allow_nan=False).encode()) > CAPABILITY['maxContextBytes']):
        raise ValueError('Bounded v1 export observations required')
    for kind, limit in (('work', 1000), ('artifacts', 1000), ('omissions', 20)):
        if not isinstance(value[kind], list) or len(value[kind]) > limit:
            raise ValueError('Export observation count exceeds its bound')
    def text(v, limit=1000):
        return isinstance(v, str) and 0 < len(v) <= limit
    for row in value['work']:
        if (not isinstance(row, dict) or set(row) - {'anchor', 'status', 'toolCalls', 'modelCalls', 'durationSeconds'}
                or not {'anchor', 'status', 'toolCalls', 'modelCalls'} <= set(row)
                or not text(row['anchor']) or row['status'] not in {'completed', 'cancelled', 'error', 'unknown'}
                or any(type(row[k]) is not int or not 0 <= row[k] <= 1000000 for k in ('toolCalls', 'modelCalls'))
                or 'durationSeconds' in row and (type(row['durationSeconds']) not in (int, float)
                    or not math.isfinite(row['durationSeconds']) or not 0 <= row['durationSeconds'] <= 31536000)):
            raise ValueError('Work summaries accept recorded status, counts and duration only')
    for row in value['artifacts']:
        if (not isinstance(row, dict) or set(row) - {'id', 'title', 'kind', 'messageId'}
                or not {'id', 'title', 'kind'} <= set(row)
                or any(not text(row[k], 4000 if k == 'title' else 1000) for k in row)):
            raise ValueError('Artifact exports accept bounded references only')
    if any(not text(code, 100) or not re.fullmatch('[a-z_]+', code) for code in value['omissions']):
        raise ValueError('Export omission codes must be bounded identifiers')
    return copy.deepcopy(value)


def timestamp(value):
    try:
        if type(value) in (int, float) and math.isfinite(value):
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat()
        if isinstance(value, str) and len(value) <= 100:
            result = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
            return result.isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def render(messages, args, identity, revision, aliases):
    flags = options(args)
    context = contribution(args.get('exportContext'))
    public = copy.deepcopy(messages)
    omitted = list(context['omissions'])
    selected = {row['id']:row for row in public}
    def anchor(value):
        return aliases.get(value, value)
    work = []
    if flags['includeExecution']:
        for item in context['work']:
            target = anchor(item.pop('anchor'))
            if target in selected:
                row = selected[target]
                row.setdefault('workSummaries', []).append(item)
                work.append(item)
            elif args.get('scope', 'all') == 'all':
                work.append(item)
        omitted.append('work_summaries_cover_retained_host_observations_only')
    artifacts = []
    for item in context['artifacts']:
        target = anchor(item.get('messageId'))
        if args.get('scope', 'all') != 'all' and target not in selected:
            continue
        if target in selected:
            item['messageId'] = target
        elif 'messageId' in item:
            item.pop('messageId')
        if not flags['includeReferenceIds']:
            item.pop('id'); item.pop('messageId', None)
        artifacts.append(item)
    for row in public:
        recorded = row.get('provenance', {}).pop('createdAt', None)
        if flags['includeTimestamps']:
            value = timestamp(recorded)
            if value is not None:
                row['timestamp'] = value
            else:
                omitted.append('some_message_timestamps_not_recorded')
        if args.get('minimal'):
            row.pop('provenance', None)
        if not flags['includeReferenceIds']:
            row.pop('id')
            if 'provenance' in row:
                row['provenance'] = {key: value for key, value in row['provenance'].items()
                                     if key in {'authorization', 'inputOrigin', 'via', 'recordedOnly'}}
            original = row.get('publicMessage')
            if original:
                original.pop('inputId', None)
                original['blocks'] = [reference_without_ids(block) for block in original['blocks']]
            if 'attachments' in row:
                row['attachments'] = [reference_without_ids(item) for item in row['attachments']]
    envelope = {**({} if not flags['includeConversationId'] else {'sessionId': identity, 'historyRevision': revision}),
                'messages': public, 'artifacts': artifacts}
    if flags['includeExecution']:
        attached = {id(item) for row in public for item in row.get('workSummaries', [])}
        other = [item for item in work if id(item) not in attached]
        envelope['workSummaryCoverage'] = 'Retained host observations only; not a complete execution audit.'
        if other:
            envelope['otherWorkSummaries'] = other
    fmt = args.get('format', 'json')
    if fmt == 'json':
        body = json.dumps(envelope, ensure_ascii=False, indent=2).encode()
    elif fmt == 'markdown':
        parts = []
        if flags['includeConversationId']:
            parts.append('Conversation: ' + identity)
        for row in public:
            parts.append('## ' + row['role'].title() + (' · ' + row['timestamp'] if 'timestamp' in row else '') + '\n\n' + row['text'])
            for item in row.get('attachments', []):
                label = item.get('name', item['type'])
                ref = item.get('uri') or (item.get('reference') or {}).get('id')
                parts.append('Attachment: ' + literal(label) + (' (' + literal(ref) + ')' if ref else '') + '; payload omitted.')
            parts.extend(work_text(item) for item in row.get('workSummaries', []))
        if envelope.get('otherWorkSummaries'):
            parts.append('## Other retained work')
            parts.extend(work_text(item) for item in envelope['otherWorkSummaries'])
        if flags['includeExecution']:
            parts.append(envelope['workSummaryCoverage'])
        if artifacts:
            parts.append('## Artifact references')
            parts.extend('- ' + literal(item['title']) + ' (' + literal(item['kind']) + ')' + (' · ' + literal(item['id']) if 'id' in item else '') for item in artifacts)
        body = ('\n\n'.join(parts) + '\n').encode()
    else:
        raise ValueError('Unsupported export format')
    return body, {'options': flags, 'executionSummaryCount': len(work), 'artifactCount': len(artifacts),
                  'omissions': [{'code': code} for code in dict.fromkeys(omitted)]}


def reference_without_ids(item):
    return {key: value for key, value in item.items()
            if key in {'type', 'name', 'mimeType', 'size', 'text', 'omittedPayload'}}


def literal(value):
    return re.sub(r'([\\`*_{}\[\]<>])', r'\\\1', ' '.join(str(value).split()))


def work_text(item):
    return ('Work: ' + item['status'] + ' · ' + str(item['toolCalls']) + ' tool calls · '
            + str(item['modelCalls']) + ' model calls'
            + (' · ' + str(round(item['durationSeconds'], 3)) + ' seconds' if 'durationSeconds' in item else '') + '.')
