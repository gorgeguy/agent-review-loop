"""Smoke tests — confirm the project is wired up correctly."""

import agent_review_loop


def test_should_have_version() -> None:
    assert agent_review_loop.__version__
