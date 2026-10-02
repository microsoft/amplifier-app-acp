"""Extracted native application runtime; provenance in extraction.json."""
def contains_input(messages, input_id):
    for message in messages:
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        metadata = message.get('metadata')
        marker = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
        if isinstance(marker, dict) and marker.get('id') == input_id:
            return True
    return False
