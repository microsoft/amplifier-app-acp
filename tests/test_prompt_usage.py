from amplifier_acp.prompt_usage import prompt_usage

def test_measured_calls_sum_and_unknown_is_not_zero():
    assert prompt_usage({}) is None
    assert prompt_usage({'one':{},'two':{'inputTokens':3,'outputTokens':2}}) is None
    assert prompt_usage({'one':{'inputTokens':3,'outputTokens':2,'cacheReadTokens':1},'two':{'inputTokens':7,'outputTokens':4,'cacheReadTokens':2}})=={'inputTokens':10,'outputTokens':6,'totalTokens':16,'cachedReadTokens':3}

def test_call_updates_replace_instead_of_double_counting():
    calls={'id':{'inputTokens':5,'outputTokens':0}}
    calls['id']={'inputTokens':5,'outputTokens':3}
    assert prompt_usage(calls)['totalTokens']==8
