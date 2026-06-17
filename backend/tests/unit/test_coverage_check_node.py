"""coverage_check_node — hard-cap short-circuit, re-plan, and fail-safe."""

from app.graph.graph import CoverageAssessment, coverage_check_node
from tests.conftest import FakeLLM, patch_llm


def _state(**overrides):
    base = {
        "query": "q",
        "sources": [{"url": "http://1", "title": "T", "content": "c"}],
        "tried_queries": ["a"],
        "iteration_count": 1,
        "tavily_call_count": 1,
    }
    base.update(overrides)
    return base


async def test_short_circuits_on_iteration_cap_without_llm(monkeypatch):
    llm = FakeLLM(structured_result=CoverageAssessment(sufficient=False, additional_queries=["x"]))
    patch_llm(monkeypatch, llm)

    out = await coverage_check_node(_state(iteration_count=3))

    assert out["coverage_sufficient"] is True
    assert llm.calls == 0  # hard cap must not consult the model


async def test_short_circuits_on_tavily_cap_without_llm(monkeypatch):
    llm = FakeLLM(structured_result=CoverageAssessment(sufficient=False, additional_queries=["x"]))
    patch_llm(monkeypatch, llm)

    out = await coverage_check_node(_state(tavily_call_count=5))

    assert out["coverage_sufficient"] is True
    assert llm.calls == 0


async def test_sufficient_assessment(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(structured_result=CoverageAssessment(sufficient=True)))

    out = await coverage_check_node(_state())

    assert out["coverage_sufficient"] is True


async def test_insufficient_proposes_new_queries(monkeypatch):
    assessment = CoverageAssessment(sufficient=False, additional_queries=["gap1", "gap2"])
    patch_llm(monkeypatch, FakeLLM(structured_result=assessment))

    out = await coverage_check_node(_state())

    assert out["coverage_sufficient"] is False
    assert out["search_queries"] == ["gap1", "gap2"]


async def test_insufficient_but_no_queries_is_treated_as_sufficient(monkeypatch):
    assessment = CoverageAssessment(sufficient=False, additional_queries=[])
    patch_llm(monkeypatch, FakeLLM(structured_result=assessment))

    out = await coverage_check_node(_state())

    # No actionable follow-up queries -> nothing to loop on -> sufficient
    assert out["coverage_sufficient"] is True


async def test_fails_safe_to_sufficient_on_exception(monkeypatch):
    patch_llm(monkeypatch, FakeLLM(exc=RuntimeError("coverage model down")))

    out = await coverage_check_node(_state())

    assert out["coverage_sufficient"] is True
