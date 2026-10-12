"""Bounded recorded failure guidance; no arbitrary provider payload storage."""
import re,time
from .configuration_clone import location
from .relocation import home,write

def generation_failure(event):
    """Project loop-live's bounded failure vocabulary, never arbitrary messages."""
    category = event.get('error_category')
    messages = {
        'unknown': ('The manager turn failed.', 'Inspect saved details before continuing. A recovery copy preserves readable history without replaying completed actions.'),
        'context_limit': ('The request could not fit within the context budget.', 'Inspect the active instructions and attachments, or choose a model with more context. Completed actions were not replayed.'),
        'context_compaction': ('Context compaction failed.', 'Original history is preserved. Repair context preparation before continuing; no foreground model request was sent for this step.'),
        'context_measurement': ('Could not check conversation size.', 'Original history and the saved checkpoint are preserved. Check the counting diagnostic before continuing.'),
        'authentication': ('The selected provider rejected its credentials.', 'Check the selected provider in Settings before continuing.'),
        'rate_limit': ('The selected provider rate limit was reached.', 'Wait for the provider limit to reset before continuing.'),
        'content_filter': ('The provider stopped the request under its content policy.', 'Review the request and the provider guidance. Recovery does not clear a safety stop.'),
        'invalid_request': ('The provider could not accept the request format.', 'Inspect the selected model, attachments and tool configuration before continuing.'),
        'provider_timeout': ('The provider request timed out; its outcome may be unknown.', 'Inspect saved results before retrying. An interrupted request may already have had effects.'),
        'provider_unavailable': ('The selected provider is unavailable.', 'Check the connection and service availability before continuing.'),
    }
    if category not in messages:
        return None
    summary, guidance = messages[category]
    stage = event.get('error_stage')
    if stage not in {'turn_setup', 'context_preparation', 'provider_request', 'manager_turn'}:
        stage = 'manager_turn'
    if category == 'context_limit' and stage == 'context_preparation':
        summary = 'Local context preparation could not fit the required content within the input budget.'
        guidance = 'Inspect the active instructions and attachments. This was a local budget check, not a provider response. Required content was not discarded; earlier actions were not replayed.'
    kind = event.get('error_type')
    kind = kind if isinstance(kind, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,99}', kind) else 'Error'
    code = event.get('error_code')
    known = {'native_input_oversized', 'native_checkpoint_invalid', 'native_no_reduction',
             'native_measurement_unavailable', 'native_compaction_failed', 'disabled',
             'request_context_unavailable', 'invalid_native_contract', 'authoritative_measurement_unavailable',
             'summary_output_limit', 'summary_empty', 'previous_failure', 'summary_failed', 'native_failed'}
    code = code if isinstance(code, str) else None
    if category == 'context_compaction':
        stage = 'context_preparation'
        if code == 'native_input_oversized':
            summary = 'Saved history is too large for native compaction.'
            guidance = 'Restore a compatible checkpoint or explicitly recover this history in bounded windows. Retrying the same request will not fix it. Original history is preserved; no foreground model request was sent for this step.'
        elif code == 'native_checkpoint_invalid':
            summary = 'The saved native checkpoint cannot be used by the selected provider or model.'
            guidance = 'Restore a compatible checkpoint or explicitly recover the history. Original history is preserved; no automatic summary fallback was used.'
        elif code in {'native_measurement_unavailable', 'authoritative_measurement_unavailable'}:
            summary = 'Could not check conversation size.'
            guidance = 'Original history and the saved checkpoint are preserved. The counting service returned no usable measurement; the underlying cause was not recorded. Try Continue conversation once. If it fails again, share diagnostics; resetting the chat is not required.'
        elif code == 'summary_output_limit':
            summary = 'The conversation summary reached its output limit.'
            guidance = 'Increase the context module’s summary_max_output_tokens allowance before continuing. No incomplete summary was saved; original history is preserved.'
        elif code == 'summary_empty':
            summary = 'The summarizer returned no usable conversation summary.'
            guidance = 'Check the summarization model and its output allowance before continuing. Original history is preserved; no empty summary was saved.'
        elif code == 'previous_failure':
            summary = 'Conversation preparation is waiting after an earlier compaction failure.'
            guidance = 'Inspect the earlier failure and correct its cause, or wait for the retry cooldown before continuing. The conversation has not advanced; repeating the same request immediately will not bypass this protection.'
        elif code in {'summary_failed', 'native_failed'}:
            summary = ('The conversation summary could not be prepared.' if code == 'summary_failed'
                       else 'The provider could not compact the conversation.')
            guidance = 'Check provider availability and the saved compaction diagnostics before continuing. Original history is preserved; no foreground model request was sent for this step.'
    count = {}
    if category == 'context_measurement':
        stage = 'context_preparation'
        raw = event.get('count_failure')
        raw = raw if isinstance(raw, dict) else {}
        reasons = {
            'timeout': 'The counting service timed out.',
            'connection': 'The counting service could not be reached.',
            'rate_limit': 'The counting service is rate limited.',
            'service': 'The counting service is temporarily unavailable.',
            'authentication': 'Check the selected AI connection’s credentials in Settings.',
            'permission': 'The selected AI connection does not have permission to count this request.',
            'quota': 'Check the selected AI connection’s quota or billing.',
            'invalid_request': 'The counting service rejected the request format. Share diagnostics so we can investigate.',
            'invalid_response': 'The counting service returned an invalid result. Share diagnostics so we can investigate.',
        }
        reason = raw.get('category')
        reason = reason if isinstance(reason, str) else None
        if reason in reasons:
            count['category'] = reason
        count['retryable'] = raw.get('retryable') is True and reason in {'timeout', 'connection', 'rate_limit', 'service'}
        for key, low, high in [('httpStatus', 400, 599), ('attempts', 1, 3)]:
            value = raw.get(key)
            if type(value) is int and low <= value <= high:
                count[key] = value
        request_id = raw.get('requestId')
        if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
            count['requestId'] = request_id
        guidance = 'Original history and the saved checkpoint are preserved. ' + reasons.get(reason, 'The counting failure needs investigation.')
        if count['retryable']:
            guidance += ' Wait briefly, then choose Continue conversation to try again.'
    return {**({'code': code} if category == 'context_compaction' and code in known else {}),
            **({'countFailure': count} if category == 'context_measurement' else {}),
            'category': category, 'errorType': kind, 'stage': stage,
            'summary': summary, 'guidance': guidance, 'effects': 'not_rolled_back',
            'replayed': False, 'retryable': count.get('retryable', False) if category == 'context_measurement' else event.get('retryable') is True}


def save(server,identity,row,input_id=None):
    if row.get('type')!='generation.failed':return
    detail=generation_failure(row)
    if detail is None:detail=generation_failure({**row,'error_category':'unknown'})
    for field,value in [('generationId',row.get('generation_id')),('inputId',input_id)]:
        if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',value):detail[field]=value
    write(location(home(server),identity)/'lifecycle-failure.json',{**detail,'recordedAt':time.time(),'source':'recorded-native-generation','current':True})

def read(server,identity):
    from .session_lifecycle import record
    path=location(home(server),identity)/'lifecycle-failure.json'
    if not path.exists():return None
    value=record(path)
    if not isinstance(value,dict) or len(str(value))>8192:raise ValueError('Recorded native failure exceeds its inspection bound')
    return value

def save_startup(server,identity,row):
    """Retain bounded setup guidance without importing private exception text."""
    from .native.session_health import failure_details
    detail=failure_details('',row.get('errorType'))
    if detail['category']=='unknown':detail=failure_details('','RuntimeStartupError')
    receipt=row.get('diagnosticReceipt')
    if isinstance(receipt,str) and re.fullmatch(r'startup-[a-f0-9]{32}\.log',receipt):
        detail['diagnosticReceipt']=receipt
    write(location(home(server),identity)/'lifecycle-failure.json',{
        **detail,'stage':'worker_startup','inputSubmitted':False,'recordedAt':time.time(),
        'source':'recorded-native-startup','current':True})

def supersede(server,identity,*,source=None):
    value=read(server,identity)
    if value and value.get('current') and (source is None or value.get('source')==source):
        write(location(home(server),identity)/'lifecycle-failure.json',{**value,'current':False})
