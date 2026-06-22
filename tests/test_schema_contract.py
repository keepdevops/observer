"""Contract parity (S6): the exported JSON Schema faithfully describes the Python envelopes.

This is the foundation of the polyglot proof — the Go S3 components validate against these same
`bus/schema/*.json` files (and their own `*FieldNames` tests assert their replies conform).
"""
import json
from pathlib import Path

from bus import schema_export as SE
from bus import subjects as S
from bus.contracts.base import SCHEMA_VERSION

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "bus" / "schema"


def test_exported_schema_has_no_drift():
    assert SE.check(SCHEMA_DIR) is True


def test_every_envelope_has_a_schema_file():
    for name in SE.render():
        assert (SCHEMA_DIR / f"{name}.json").exists(), f"missing schema for {name}"


def test_schema_version_file_is_pinned():
    assert (SCHEMA_DIR / "VERSION").read_text().strip() == SCHEMA_VERSION


def test_sample_instances_round_trip():
    samples = [
        S.InferRequest(request_id="r", model="m", prompt="p"),
        S.Token(request_id="r", model="m", done=True, tokens=5),
        S.Announce(component_id="c", info=S.ModelInfo(name="m"), infer_subject="x"),
        S.Presence(component_id="c", status=S.Status.online),
    ]
    for inst in samples:
        assert type(inst).model_validate_json(inst.model_dump_json()) == inst


def test_serialized_instance_carries_every_required_property():
    inst = S.InferRequest(request_id="r", model="m", prompt="p")
    schema = json.loads((SCHEMA_DIR / "InferRequest.json").read_text())
    data = json.loads(inst.model_dump_json())
    for prop in schema.get("required", []):
        assert prop in data, f"instance missing required '{prop}'"


def test_every_envelope_stamps_schema_version():
    for inst in (S.Goodbye(component_id="c"), S.Cancel(request_id="r"),
                 S.InferResponse(request_id="r", model="m")):
        assert json.loads(inst.model_dump_json())["schema_version"] == SCHEMA_VERSION
