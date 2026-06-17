"""Event-driven presence state-machine tests (no broker, no heartbeat)."""
from bus import subjects as S
from bus.presence import Presence


def _announce(p: Presence, cid: str, name: str):
    return p.apply_announce({
        "component_id": cid, "kind": "model",
        "info": {"name": name}, "infer_subject": S.model_subject(name),
    })


def test_announce_online_and_subject():
    p = Presence()
    assert _announce(p, "c1", "echo") is not None
    assert p.models() == ["echo"]
    assert p.model_subject("echo") == S.model_subject("echo")
    assert p.model_component("echo") == "c1"


def test_goodbye_marks_offline():
    p = Presence()
    _announce(p, "c1", "echo")
    p.apply_goodbye({"component_id": "c1"})
    assert p.models() == []
    assert p.model_subject("echo") is None


def test_mark_down_removes_model():
    p = Presence()
    _announce(p, "c1", "echo")
    p.mark_down("c1", "no responder")
    assert p.models() == []


def test_malformed_announce_rejected():
    p = Presence()
    assert p.apply_announce({"component_id": "x"}) is None  # missing info/infer_subject
    assert p.models() == []


def test_snapshot_returns_online_only():
    p = Presence()
    _announce(p, "c1", "a")
    _announce(p, "c2", "b")
    p.mark_down("c2", "gone")
    names = {s.info.name for s in p.snapshot()}
    assert names == {"a"}
