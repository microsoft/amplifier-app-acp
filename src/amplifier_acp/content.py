"""ACP content to existing native attachment records; no provider-specific format."""
import base64,json,os,uuid
from pathlib import Path
from .native.attachments import MAX_BYTES,MAX_FILES,_IMAGE_MIMES,_MIMES,location

def attachment(config,block,*,image_only=True):
    mime=block.get('mimeType')
    if image_only and mime not in _IMAGE_MIMES:raise ValueError('Unsupported native image MIME type.')
    if mime not in _MIMES:mime='application/octet-stream'
    try:payload=base64.b64decode(block.get('data',''),validate=True)
    except (ValueError,TypeError):raise ValueError('Image content must contain valid base64 data.')
    if not payload or len(payload)>MAX_BYTES:raise ValueError('Image attachment exceeds the native size limit.')
    home=Path(config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
    identity=uuid.uuid4().hex;directory=location(home,identity)
    directory.mkdir(parents=True,mode=0o700)
    row={'id':identity,'name':'ACP image','mime':mime,'size':len(payload)}
    for name,data in [('content',payload),('metadata.json',json.dumps(row).encode())]:
        fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    return row

def image_attachment(config,block):
    return attachment(config,block)

def embedded_attachment(config,resource):
    return attachment(config,{'data':resource.get('blob'),'mimeType':resource.get('mimeType','application/octet-stream')},image_only=False)


def prompt_content(config, blocks):
    from .server import RpcError
    text=[];attachments=[]
    for block in blocks:
        if not isinstance(block,dict):raise RpcError(-32602,'Content blocks must be objects.')
        if block.get('type')=='text' and isinstance(block.get('text'),str):text.append(block['text'])
        elif block.get('type')=='image':
            if len(attachments)>=8:raise RpcError(-32602,'Attach up to 8 images per prompt.')
            attachments.append(image_attachment(config,block))
        elif block.get('type')=='resource' and isinstance(block.get('resource'),dict) and isinstance(block['resource'].get('text'),str):
            if len(attachments)>=MAX_FILES:raise RpcError(-32602,'Attach up to 8 resources per prompt.')
            resource=block['resource']
            # Text resources are files too. Flattening them into the user text
            # bypasses attachment handling and trips the short-message limit.
            attachments.append(embedded_attachment(config,{
                'mimeType':resource.get('mimeType','text/plain'),
                'blob':base64.b64encode(resource['text'].encode('utf-8')).decode('ascii')}))
            text.append('Embedded resource: '+str(resource.get('uri','')))
        elif block.get('type')=='resource' and isinstance(block.get('resource'),dict) and isinstance(block['resource'].get('blob'),str):
            if len(attachments)>=8:raise RpcError(-32602,'Attach up to 8 resources per prompt.')
            attachments.append(embedded_attachment(config,block['resource']))
            text.append('Embedded resource: '+str(block['resource'].get('uri','')))
        elif block.get('type')=='resource_link' and isinstance(block.get('uri'),str):
            # Explicit quoted reference, interpreted by configured tools; no implicit network read.
            text.append('Referenced resource: '+json.dumps({k:block[k] for k in ('uri','name','description','mimeType') if k in block},ensure_ascii=False))
        else:raise RpcError(-32602,'This content type is not advertised by the adapter.',{'type':block.get('type')})
    value='\n'.join(text) or ('[Image attachment]' if attachments else '')
    if not value.strip():raise RpcError(-32602,'A nonempty prompt is required.')
    return value, attachments
