"""call_gemini_followup — content normalisation and prompt assembly."""

from app.graph.graph import call_gemini_followup
from tests.conftest import FakeLLM, patch_llm


async def test_returns_string_content(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content="The answer."))

    answer = await call_gemini_followup(
        query="q",
        report_markdown="# report",
        sources=[{"title": "T", "url": "http://1", "content": "c"}],
        prior_turns=[],
        question="Why?",
    )

    assert answer == "The answer."


async def test_list_content_is_joined(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(content=["part1 ", "part2"]))

    answer = await call_gemini_followup(
        query="q",
        report_markdown="# report",
        sources=[],
        prior_turns=[{"turn_number": 1, "question": "Q1", "answer": "A1"}],
        question="And then?",
    )

    assert answer == "part1 part2"
