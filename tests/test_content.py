import base64,json
import pytest
from amplifier_acp.content import image_attachment,embedded_attachment,prompt_content
from amplifier_acp.native.attachments import file_path

def test_native_content_keeps_bytes_in_private_storage(tmp_path):
    config={'appHome':str(tmp_path)}
    image=image_attachment(config,{'mimeType':'image/png','data':base64.b64encode(b'fixture-image-bytes').decode()})
    path,row=file_path(tmp_path,image['id']);assert path.read_bytes()==b'fixture-image-bytes';assert path.stat().st_mode&0o077==0
    document=embedded_attachment(config,{'mimeType':'application/pdf','blob':base64.b64encode(b'fixture-pdf-bytes').decode()})
    assert file_path(tmp_path,document['id'])[0].read_bytes()==b'fixture-pdf-bytes'
    with pytest.raises(ValueError):image_attachment(config,{'mimeType':'image/png','data':'invalid%%%'})

def test_prompt_and_edit_share_text_reference_and_binary_decoding(tmp_path):
    value,attachments=prompt_content({'appHome':str(tmp_path)},[
        {'type':'text','text':'New request'},
        {'type':'resource','resource':{'uri':'resource://saved-note','text':'Saved note','mimeType':'text/plain'}},
        {'type':'resource_link','uri':'https://invalid.example/no-network-read','name':'Link'},
        {'type':'image','mimeType':'image/png','data':base64.b64encode(b'image').decode()},
        {'type':'resource','resource':{'uri':'resource://saved-pdf','mimeType':'application/pdf','blob':base64.b64encode(b'pdf').decode()}},
    ])
    assert value.startswith('New request\nEmbedded resource: resource://saved-note')
    assert 'Saved note' not in value and 'https://invalid.example/no-network-read' in value
    assert 'Embedded resource: resource://saved-pdf' in value
    assert [file_path(tmp_path,item['id'])[0].read_bytes() for item in attachments]==[b'Saved note',b'image',b'pdf']
    assert [item['mime'] for item in attachments]==['text/plain','image/png','application/pdf']

@pytest.mark.parametrize('blocks',[[],[None],[{'type':'audio','data':'invalid'}],[{'type':'resource','resource':{}}]])
def test_unsupported_or_empty_replacement_content_is_refused(tmp_path,blocks):
    from amplifier_acp.server import RpcError
    with pytest.raises(RpcError):prompt_content({'appHome':str(tmp_path)},blocks)


def test_large_unicode_text_resource_uses_file_path_not_short_message_budget(tmp_path, monkeypatch):
    from amplifier_acp.native.attachments import encode
    from types import SimpleNamespace
    content='Corrected file contents Ω\n'*20000
    text,files=prompt_content({'appHome':str(tmp_path)},[
        {'type':'text','text':'Use this correction'},
        {'type':'resource','resource':{'uri':'resource://file','text':content,'mimeType':'text/plain'}}])
    assert len(text)<16000
    assert file_path(tmp_path,files[0]['id'])[0].read_bytes()==content.encode('utf-8')
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    encoded=encode(SimpleNamespace(text=text,attachments=files))
    assert any('Local file:' in row.get('text','') for row in encoded)
    assert not any(content in row.get('text','') for row in encoded)
    from amplifier_acp.server import RpcError
    with pytest.raises(RpcError,match='8 resources'):
        prompt_content({'appHome':str(tmp_path)},[{'type':'resource','resource':{'uri':'resource://file','text':'x'}}]*9)
