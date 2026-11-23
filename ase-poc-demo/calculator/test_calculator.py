import pytest

from calculator import add, evaluate


def test_add() -> None:
    assert add(1, 2) == 3


def test_scientific_notation_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        evaluate("1.5e2")
