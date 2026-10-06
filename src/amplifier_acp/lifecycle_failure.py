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
    return {'category': category, 'errorType': kind, 'stage': stage,
            'summary': summary, 'guidance': guidance, 'effects': 'not_rolled_back',
            'replayed': False, 'retryable': event.get('retryable') is True}

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
