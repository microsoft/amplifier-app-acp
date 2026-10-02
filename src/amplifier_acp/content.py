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
