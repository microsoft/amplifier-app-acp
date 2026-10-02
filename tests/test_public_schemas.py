import json
from pathlib import Path
import jsonschema
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('name',['native-request','native-history-request','native-history-result','host-request','native-admin-request','host-capabilities'])
def test_public_native_fixtures(name):
    schema=json.loads((ROOT/'schemas'/f'{name}.v1.json').read_text())
    assert schema['$id'].startswith('https://microsoft.github.io/amplifier-acp/')
    value=json.loads((ROOT/'fixtures'/f'{name}.v1.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(value)
