"""Offline JSON-RPC MCP peer: actual MCP transport and module exercise this process."""
import json,sys
for line in sys.stdin:
    row=json.loads(line)
    if 'id' not in row:continue
    method=row.get('method')
    if method=='initialize':result={'protocolVersion':row['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
    elif method=='tools/list':result={'tools':[{'name':'echo','description':'Return fixture text','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
    elif method=='tools/call':result={'content':[{'type':'text','text':row['params']['arguments']['text']}]}
    elif method=='resources/list':result={'resources':[]}
    elif method=='resources/templates/list':result={'resourceTemplates':[]}
    elif method=='prompts/list':result={'prompts':[]}
    else:result={}
    print(json.dumps({'jsonrpc':'2.0','id':row['id'],'result':result}),flush=True)
