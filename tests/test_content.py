import base64,json
import pytest
from amplifier_acp.content import image_attachment,embedded_attachment
from amplifier_acp.native.attachments import file_path

def test_native_content_keeps_bytes_in_private_storage(tmp_path):
    config={'appHome':str(tmp_path)}
    image=image_attachment(config,{'mimeType':'image/png','data':base64.b64encode(b'fixture-image-bytes').decode()})
    path,row=file_path(tmp_path,image['id']);assert path.read_bytes()==b'fixture-image-bytes';assert path.stat().st_mode&0o077==0
    document=embedded_attachment(config,{'mimeType':'application/pdf','blob':base64.b64encode(b'fixture-pdf-bytes').decode()})
    assert file_path(tmp_path,document['id'])[0].read_bytes()==b'fixture-pdf-bytes'
    with pytest.raises(ValueError):image_attachment(config,{'mimeType':'image/png','data':'invalid%%%'})
