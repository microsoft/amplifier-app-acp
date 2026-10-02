"""Extracted native application runtime; provenance in extraction.json."""
import json


def compact(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
