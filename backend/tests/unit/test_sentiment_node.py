"""sentiment_node — VADER scoring and the fail-soft empty cases."""

from app.graph.graph import sentiment_node
from tests.conftest import make_fake_youtube, patch_youtube


def _state(**overrides):
    base = {"query": "q", "youtube_search_query": "topic"}
    base.update(overrides)
    return base


async def test_scores_comments_and_returns_volume(monkeypatch):
    youtube = make_fake_youtube(
        video_ids=["v1"],
        comments_by_video={
            "v1": [
                "I absolutely love this, it is amazing and wonderful!",
                "This is terrible, I hate it, awful and disappointing.",
                "This is a video about the topic.",
            ]
        },
    )
    patch_youtube(monkeypatch, youtube)

    out = await sentiment_node(_state())

    scores = out["sentiment_scores"]
    assert out["sentiment_volume"] == 3
    assert set(scores) == {"positive", "neutral", "negative"}
    # scores are each rounded to 4dp in the node, so the sum can drift slightly off 1.0
    assert abs(scores["positive"] + scores["neutral"] + scores["negative"] - 1.0) < 1e-3
    assert scores["positive"] > 0
    assert scores["negative"] > 0


async def test_no_videos_returns_none(monkeypatch):
    patch_youtube(monkeypatch, make_fake_youtube(video_ids=[], comments_by_video={}))

    out = await sentiment_node(_state())

    assert out["sentiment_scores"] is None
    assert out["sentiment_volume"] is None


async def test_no_comments_returns_none(monkeypatch):
    youtube = make_fake_youtube(video_ids=["v1"], comments_by_video={"v1": []})
    patch_youtube(monkeypatch, youtube)

    out = await sentiment_node(_state())

    assert out["sentiment_scores"] is None
    assert out["sentiment_volume"] is None


async def test_exception_is_non_fatal(monkeypatch):
    from app.graph import graph

    def boom(*_a, **_k):
        raise RuntimeError("youtube api down")

    monkeypatch.setattr(graph, "build", boom)

    out = await sentiment_node(_state())

    assert out["sentiment_scores"] is None
    assert out["sentiment_volume"] is None
