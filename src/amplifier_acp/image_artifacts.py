"""Deliver actual completed image tool results through an advertised Canvas action.

This observes live execution, never scans history or regenerates an image.
The Resources owner must check the digest while saving its immutable bytes.
"""
import hashlib
import re


def completed_artifact(event, *, metadata=False):
    if event.get('kind') != 'tool' or event.get('label') != 'image_generate' or event.get('phase') != 'completed':
        return None
    result = event.get('output')
    if not isinstance(result, dict) or result.get('success') is not True:
        return None
    receipt = result.get('output')
    if not isinstance(receipt, dict) or receipt.get('schema') != 'amplifier.image.receipt.v1' or receipt.get('status') != 'completed':
        return None
    if receipt.get('operation') not in {'generate', 'edit'}:
        return None
    artifact = receipt.get('artifact')
    if not isinstance(artifact, dict) or artifact.get('mimeType') != 'image/png':
        return None
    for value in (receipt.get('requestHash'), artifact.get('sha256')):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
            return None
    for value in (receipt.get('requestId'), artifact.get('path'), event.get('toolCallId'), event.get('sessionId')):
        if not isinstance(value, str) or not 0 < len(value) <= 500 or '\0' in value:
            return None
    identity = hashlib.sha256((event['sessionId'] + '\0' + event['toolCallId'] + '\0' + receipt['requestHash']).encode()).hexdigest()
    return {'action': 'canvas.openFile', 'id': 'image-result:' + identity, 'args': {
        'kind': 'image', 'title': 'Edited image' if receipt['operation'] == 'edit' else 'Generated image',
        'path': artifact['path'], 'expectedSha256': artifact['sha256'],
        **({'imageResult': {'version': 1, 'toolCallId': event['sessionId'] + ':' + event['toolCallId'],
            'requestId': receipt['requestId'], 'operation': receipt['operation']}} if metadata else {})}}


async def deliver(session, event):
    # The root session and active input are execution authority, not fields
    # supplied by a tool result or a replayed history row.
    if event.get('sessionId') != session.identity or not session.prompt or session.prompt.done():
        return
    if session.server.host_info.get('features', {}).get('imageResultArtifacts') is not True:
        return
    metadata_enabled = session.server.host_info.get('features', {}).get('imageResultMetadataV1') is True
    request = completed_artifact(event, metadata=metadata_enabled)
    if request is None:
        return
    try:
        result = await session.bridge('dispatch', request)
        if not isinstance(result, dict) or result.get('accepted') is not True:
            raise ValueError('Saved image attachment was not confirmed')
        return 'Generated image saved in this conversation.'
    except Exception:
        # A display failure must not turn a successful generation into another
        # billable request or fail the conversation. Keep the original result.
        return 'The image was generated, but its saved preview could not be attached. The original file is preserved.'
