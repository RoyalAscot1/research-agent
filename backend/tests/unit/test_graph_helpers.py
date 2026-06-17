"""Pure-function helpers in graph.py — no LLM, no DB."""

from app.graph.graph import (
    _content_to_str,
    _derive_overall_sentiment,
    _format_sentiment_section,
    _format_sources,
)


class TestContentToStr:
    def test_plain_string_passes_through(self):
        assert _content_to_str("hello") == "hello"

    def test_list_of_strings_is_joined(self):
        assert _content_to_str(["a", "b", "c"]) == "abc"

    def test_list_with_non_string_part_is_stringified(self):
        assert _content_to_str(["a", {"x": 1}]) == "a{'x': 1}"

    def test_empty_string(self):
        assert _content_to_str("") == ""


class TestFormatSources:
    def test_numbers_sources_from_one(self):
        out = _format_sources(
            [
                {"title": "T1", "url": "http://a", "content": "c1"},
                {"title": "T2", "url": "http://b", "content": "c2"},
            ]
        )
        assert "[Source 1] T1" in out
        assert "[Source 2] T2" in out

    def test_missing_fields_use_defaults(self):
        out = _format_sources([{}])
        assert "[Source 1] Untitled" in out


class TestFormatSentimentSection:
    def test_empty_when_no_scores(self):
        assert _format_sentiment_section(None, 10) == ""

    def test_empty_when_no_volume(self):
        assert _format_sentiment_section({"positive": 0.5}, None) == ""

    def test_renders_percentages_and_volume(self):
        out = _format_sentiment_section({"positive": 0.5, "neutral": 0.3, "negative": 0.2}, 42)
        assert "Positive: 50.0%" in out
        assert "Neutral: 30.0%" in out
        assert "Negative: 20.0%" in out
        assert "Comments analysed: 42" in out


class TestDeriveOverallSentiment:
    def test_none_when_no_scores(self):
        assert _derive_overall_sentiment(None) is None

    def test_positive_above_55(self):
        assert _derive_overall_sentiment({"positive": 0.56, "negative": 0.1}) == "Positive"

    def test_negative_above_30(self):
        assert _derive_overall_sentiment({"positive": 0.4, "negative": 0.31}) == "Negative"

    def test_mixed_otherwise(self):
        assert _derive_overall_sentiment({"positive": 0.5, "negative": 0.2}) == "Mixed"

    def test_positive_takes_precedence_over_negative(self):
        # positive > 0.55 wins even if negative is also high
        assert _derive_overall_sentiment({"positive": 0.6, "negative": 0.35}) == "Positive"

    def test_boundary_exactly_55_is_not_positive(self):
        assert _derive_overall_sentiment({"positive": 0.55, "negative": 0.0}) == "Mixed"
