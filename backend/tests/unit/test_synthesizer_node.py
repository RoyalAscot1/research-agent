"""synthesizer_node — the empty-output and timeout failure paths plus the happy case.

These guard the two subtle gotchas documented in CLAUDE.md: a falsy error string is
treated as success by run_graph, so an empty report and an asyncio.TimeoutError (whose
str() is "") must both produce a NON-empty error.
"""

import asyncio

from app.graph.graph import synthesizer_node
from tests.conftest import FakeLLM, patch_llm


def _state(**overrides):
    base = {
        "query": "q",
        "sources": [{"url": "http://1", "title": "T", "content": "c"}],
        "sentiment_scores": None,
        "sentiment_volume": None,
    }
    base.update(overrides)
    return base


async def test_happy_path_sets_markdown(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content="# Final Report\n\nBody."))

    out = await synthesizer_node(_state())

    assert out["report_markdown"] == "# Final Report\n\nBody."
    assert out["error"] is None


async def test_list_content_is_joined(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content=["# Part A ", "Part B"]))

    out = await synthesizer_node(_state())

    assert out["report_markdown"] == "# Part A Part B"
    assert out["error"] is None


async def test_empty_content_is_a_failure(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content=""))

    out = await synthesizer_node(_state())

    assert out["report_markdown"] is None
    assert out["error"] == "Synthesizer produced an empty report"


async def test_whitespace_only_content_is_a_failure(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content="   \n\t  "))

    out = await synthesizer_node(_state())

    assert out["report_markdown"] is None
    assert out["error"] == "Synthesizer produced an empty report"


async def test_timeout_yields_non_empty_error(monkeypatch):
    # str(asyncio.TimeoutError()) == "" — error must fall back to the class name
    # so run_graph's `if state["error"]:` registers it as a failure.
    patch_llm(monkeypatch, FakeLLM(exc=asyncio.TimeoutError()))

    out = await synthesizer_node(_state())

    assert out["report_markdown"] is None
    assert out["error"] == "TimeoutError"
    assert out["error"]  # explicitly: truthy


async def test_works_without_sources(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content="# No-sources report"))

    out = await synthesizer_node(_state(sources=[]))

    assert out["report_markdown"] == "# No-sources report"
    assert out["error"] is None
