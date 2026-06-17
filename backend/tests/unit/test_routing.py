"""The conditional edge after coverage_check — _route_after_coverage."""

from app.graph.graph import _route_after_coverage


def _state(**overrides):
    base = {
        "coverage_sufficient": True,
        "iteration_count": 1,
        "tavily_call_count": 1,
        "run_sentiment": False,
    }
    base.update(overrides)
    return base


def test_loops_back_when_insufficient_and_under_caps():
    state = _state(coverage_sufficient=False, iteration_count=1, tavily_call_count=2)
    assert _route_after_coverage(state) == "researcher"


def test_no_loop_when_iteration_cap_hit():
    state = _state(coverage_sufficient=False, iteration_count=3, tavily_call_count=2)
    # caps exhausted -> exit loop; run_sentiment False -> synthesizer
    assert _route_after_coverage(state) == "synthesizer"


def test_no_loop_when_tavily_cap_hit():
    state = _state(coverage_sufficient=False, iteration_count=1, tavily_call_count=5)
    assert _route_after_coverage(state) == "synthesizer"


def test_sufficient_with_sentiment_routes_to_sentiment():
    state = _state(coverage_sufficient=True, run_sentiment=True)
    assert _route_after_coverage(state) == "sentiment"


def test_sufficient_without_sentiment_routes_to_synthesizer():
    state = _state(coverage_sufficient=True, run_sentiment=False)
    assert _route_after_coverage(state) == "synthesizer"


def test_exiting_loop_still_honours_sentiment_branch():
    # cap hit so no re-loop, but sentiment was requested
    state = _state(coverage_sufficient=False, iteration_count=3, run_sentiment=True)
    assert _route_after_coverage(state) == "sentiment"
