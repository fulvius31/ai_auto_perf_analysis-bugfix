"""Tests for auto_bug_fix.run_bug_fix — pipeline state and strategy definitions."""
from auto_bug_fix.run_bug_fix import (
    PipelineState,
    PipelineStop,
    PipelineEscalation,
    CHERRY_PICK_STRATEGIES,
)


def test_pipeline_state_defaults():
    state = PipelineState()
    assert state.seed == []
    assert state.allowed_modules == []
    assert state.baseline == set()
    assert state.escalated_paths == []
    assert state.fixture_cache == ""
    assert state.ported_test_files == []
    assert state.s_target == ""
    assert state.s_parent == ""
    assert state.bisect_sha is None
    assert state.cherry_pick_path == ""
    assert state.dossier is None


def test_pipeline_stop_exception():
    try:
        raise PipelineStop("fix already present")
    except PipelineStop as exc:
        assert "fix already present" in str(exc)


def test_pipeline_escalation_exception():
    try:
        raise PipelineEscalation("human intervention required")
    except PipelineEscalation as exc:
        assert "human intervention required" in str(exc)


def test_cherry_pick_strategies_order():
    assert len(CHERRY_PICK_STRATEGIES) == 3
    assert CHERRY_PICK_STRATEGIES[0]["name"] == "default"
    assert CHERRY_PICK_STRATEGIES[1]["name"] == "patience"
    assert CHERRY_PICK_STRATEGIES[2]["name"] == "ort"
