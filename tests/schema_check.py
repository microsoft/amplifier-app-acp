import json
from pathlib import Path
import jsonschema
SCHEMA=json.loads((Path(__file__).parent/'schema/acp-v1.json').read_text())
def validate(name,value):
    jsonschema.Draft202012Validator({'$ref':'#/$defs/'+name,'$defs':SCHEMA['$defs']}).validate(value)
