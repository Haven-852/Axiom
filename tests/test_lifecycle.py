import pytest

from ase.execution import assert_transition
from ase.infra.policy import classify_command, review_command


def test_execution_transitions_are_explicit() -> None:
    assert_transition("created", "starting")
    assert_transition("starting", "running")
    assert_transition("running", "completed")
    assert_transition("running", "timeout")
    assert_transition("interrupted", "running")
    with pytest.raises(ValueError):
        assert_transition("completed", "running")
    with pytest.raises(ValueError):
        assert_transition("succeeded", "running")


def test_command_levels_are_enforced_by_runtime() -> None:
    assert classify_command("python -m pytest -q") == "L2"
    assert review_command("python -m pytest -q").allowed
    decision = review_command("sudo rm -rf /")
    assert not decision.allowed
    assert decision.reason.startswith("L3")
    assert classify_command("git merge main") == "L3"
