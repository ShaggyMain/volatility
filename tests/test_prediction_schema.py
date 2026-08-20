import json
from pathlib import Path
from jsonschema import Draft202012Validator


def test_prediction_schema_loads():
    schema = json.loads(Path("schemas/prediction.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
