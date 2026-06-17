"""planner_node — structured-output happy path and the fallback on failure."""

from app.graph.graph import ResearchPlan, planner_node
from tests.conftest import FakeLLM, patch_llm


def _base_state(query="best wireless earbuds 2026"):
    return {
        "job_id": "j",
        "user_id": "u",
        "query": query,
        "search_queries": None,
        "run_sentiment": None,
        "youtube_search_query": None,
        "sources": None,
        "sentiment_scores": None,
        "sentiment_volume": None,
        "report_markdown": None,
        "error": None,
        "tavily_call_count": 0,
        "iteration_count": 0,
        "coverage_sufficient": None,
        "tried_queries": [],
    }


async def test_happy_path_applies_plan(monkeypatch):
    plan = ResearchPlan(
        search_queries=["earbuds review 2026", "earbuds battery life"],
        run_sentiment=True,
        youtube_search_query="best earbuds 2026",
    )
    patch_llm(monkeypatch, FakeLLM(structured_result=plan))

    out = await planner_node(_base_state())

    assert out["search_queries"] == ["earbuds review 2026", "earbuds battery life"]
    assert out["run_sentiment"] is True
    assert out["youtube_search_query"] == "best earbuds 2026"


async def test_fallback_on_structured_output_failure(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(exc=RuntimeError("structured output blew up")))

    out = await planner_node(_base_state(query="raw query"))

    # Falls back to the raw query as a single search, no sentiment
    assert out["search_queries"] == ["raw query"]
    assert out["run_sentiment"] is False
    assert out["youtube_search_query"] == "raw query"
