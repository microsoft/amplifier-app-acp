"""Extracted native application runtime; provenance in extraction.json."""
import json


MAX_MESSAGE_BYTES = 48_000_000


def encode_message(data):
    encoded = (json.dumps(data, ensure_ascii=False, default=str) + '\n').encode('utf-8')
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError('Runtime message exceeds the 48 MB transport limit. Request a smaller state page.')
    return encoded
