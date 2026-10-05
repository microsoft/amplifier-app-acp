"""Translate measured native calls into ACP's prompt usage totals."""
def prompt_usage(calls):
    rows=list(calls.values())
    if not rows:return None
    # Missing telemetry is unknown, never zero consumption.
    if any(any(type(row.get(key)) is not int or row[key]<0 for key in ('inputTokens','outputTokens')) for row in rows):return None
    result={key:sum(row[key] for row in rows) for key in ('inputTokens','outputTokens')}
    result['totalTokens']=sum(row.get('totalTokens',row['inputTokens']+row['outputTokens']) for row in rows)
    for source,target in (('cacheReadTokens','cachedReadTokens'),('cacheWriteTokens','cachedWriteTokens'),('reasoningTokens','thoughtTokens')):
        if all(type(row.get(source)) is int and row[source]>=0 for row in rows):result[target]=sum(row[source] for row in rows)
    return result
