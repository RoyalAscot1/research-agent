"""researcher_node — dedup, accumulation across iterations, and the 5-call hard cap."""

from app.graph.graph import researcher_node
from tests.conftest import FakeTavily, patch_tavily


def _state(**overrides):
    base = {
        "query": "q",
        "search_queries": ["a"],
        "sources": None,
        "tavily_call_count": 0,
        "iteration_count": 0,
        "tried_queries": [],
    }
    base.update(overrides)
    return base


def _result(url, title="T"):
    return {"url": url, "title": title, "content": "c"}


async def test_dedupes_results_by_url(monkeypatch):
    fake = FakeTavily(
        results_by_query={
            "a": [_result("http://1"), _result("http://2")],
            "b": [_result("http://2"), _result("http://3")],  # http://2 overlaps
        }
    )
    patch_tavily(monkeypatch, fake)

    out = await researcher_node(_state(search_queries=["a", "b"]))

    urls = sorted(s["url"] for s in out["sources"])
    assert urls == ["http://1", "http://2", "http://3"]


async def test_accumulates_onto_existing_sources(monkeypatch):
    fake = FakeTavily(results_by_query={"a": [_result("http://new")]})
    patch_tavily(monkeypatch, fake)

    out = await researcher_node(_state(search_queries=["a"], sources=[_result("http://old")]))

    urls = sorted(s["url"] for s in out["sources"])
    assert urls == ["http://new", "http://old"]


async def test_increments_counts_and_records_tried_queries(monkeypatch):
    fake = FakeTavily(default_results=[_result("http://x")])
    patch_tavily(monkeypatch, fake)

    out = await researcher_node(_state(search_queries=["a", "b"]))

    assert out["tavily_call_count"] == 2
    assert out["tried_queries"] == ["a", "b"]
    assert out["iteration_count"] == 1
    assert fake.calls == ["a", "b"]


async def test_respects_5_call_hard_cap(monkeypatch):
    fake = FakeTavily(default_results=[_result("http://x")])
    patch_tavily(monkeypatch, fake)

    # Already made 4 calls; 3 new queries but only 1 slot left before the cap of 5.
    out = await researcher_node(_state(search_queries=["a", "b", "c"], tavily_call_count=4))

    assert out["tavily_call_count"] == 5
    assert fake.calls == ["a"]  # b and c never issued
    assert out["tried_queries"] == ["a"]


async def test_tried_queries_accumulate_across_rounds(monkeypatch):
    fake = FakeTavily(default_results=[_result("http://x")])
    patch_tavily(monkeypatch, fake)

    out = await researcher_node(
        _state(search_queries=["round2"], tried_queries=["round1"], tavily_call_count=1)
    )

    assert out["tried_queries"] == ["round1", "round2"]


async def test_subquery_failure_is_non_fatal(monkeypatch):
    fake = FakeTavily(results_by_query={"good": [_result("http://ok")]})

    def boom_for_bad(query, **_kw):
        fake.calls.append(query)
        if query == "bad":
            raise RuntimeError("tavily error")
        return {"results": fake.results_by_query.get(query, [])}

    fake.search = boom_for_bad
    patch_tavily(monkeypatch, fake)

    out = await researcher_node(_state(search_queries=["bad", "good"]))

    # bad raised but good still contributed; both counted toward the cap
    assert [s["url"] for s in out["sources"]] == ["http://ok"]
    assert out["tavily_call_count"] == 2
