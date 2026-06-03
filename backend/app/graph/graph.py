import uuid
from datetime import datetime, timezone
from typing import TypedDict

from googleapiclient.discovery import build
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from tavily import TavilyClient
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.models import Report, ResearchJob


class GraphState(TypedDict):
    job_id: str
    user_id: str
    query: str
    sources: list[dict] | None
    sentiment_scores: dict | None       # {"positive": float, "neutral": float, "negative": float}
    sentiment_volume: int | None        # number of comments analysed
    report_markdown: str | None
    error: str | None


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_PROMPT = """\
You are a research assistant. Write a thorough, well-structured markdown report answering the following query:

{query}

Use the web search results below as your primary sources. Cite them where relevant using [Source N] notation.

## Web Search Results

{sources}

{sentiment_section}
---

Include: key findings, background context, and a brief summary. Use markdown headings and bullet points where appropriate.\
"""

_PROMPT_NO_SOURCES = """\
You are a research assistant. Write a thorough, well-structured markdown report answering the following query:

{query}

{sentiment_section}

Include: key findings, background context, and a brief summary. Use markdown headings and bullet points where appropriate.\
"""

_SENTIMENT_BLOCK = """\
## Public Sentiment (YouTube Comments)

Positive: {positive}% | Neutral: {neutral}% | Negative: {negative}%
Comments analysed: {volume}

Incorporate this sentiment signal where relevant — note whether public opinion is broadly positive, negative, or divided on the topic.\
"""


def _format_sources(sources: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sources, 1):
        title = s.get("title", "Untitled")
        url = s.get("url", "")
        content = s.get("content", "")
        lines.append(f"[Source {i}] {title}\nURL: {url}\n{content}")
    return "\n\n".join(lines)


def _format_sentiment_section(scores: dict | None, volume: int | None) -> str:
    if not scores or not volume:
        return ""
    return _SENTIMENT_BLOCK.format(
        positive=round(scores["positive"] * 100, 1),
        neutral=round(scores["neutral"] * 100, 1),
        negative=round(scores["negative"] * 100, 1),
        volume=volume,
    )


def _derive_overall_sentiment(scores: dict | None) -> str | None:
    """Positive if positive% > 55%, Negative if negative% > 30%, else Mixed."""
    if not scores:
        return None
    if scores["positive"] > 0.55:
        return "Positive"
    if scores["negative"] > 0.30:
        return "Negative"
    return "Mixed"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def tavily_node(state: GraphState) -> GraphState:
    try:
        client = TavilyClient(api_key=settings.tavily_api_key)
        response = client.search(
            query=state["query"],
            search_depth="basic",
            max_results=8,
            include_answer=False,
        )
        sources = response.get("results", [])
        return {**state, "sources": sources, "error": None}
    except Exception:
        # Non-fatal: proceed without sources
        return {**state, "sources": [], "error": None}


async def sentiment_node(state: GraphState) -> GraphState:
    """
    Fetches top YouTube comments for the query and scores them with VADER.
    Non-fatal — failures return empty sentiment and the graph continues.
    """
    try:
        youtube = build("youtube", "v3", developerKey=settings.youtube_api_key)

        # Step 1: search for top 5 relevant videos
        search_response = youtube.search().list(
            q=state["query"],
            part="id",
            type="video",
            maxResults=5,
            order="relevance",
            videoCaption="any",
        ).execute()

        video_ids = [
            item["id"]["videoId"]
            for item in search_response.get("items", [])
            if item["id"].get("videoId")
        ]

        if not video_ids:
            return {**state, "sentiment_scores": None, "sentiment_volume": None}

        # Step 2: fetch top comments for each video (up to 20 per video)
        comments: list[str] = []
        for video_id in video_ids:
            try:
                comments_response = youtube.commentThreads().list(
                    part="snippet",
                    videoId=video_id,
                    maxResults=20,
                    order="relevance",
                    textFormat="plainText",
                ).execute()
                for item in comments_response.get("items", []):
                    text = (
                        item["snippet"]["topLevelComment"]["snippet"].get("textDisplay", "")
                    )
                    if text:
                        comments.append(text)
            except Exception:
                # Comments may be disabled on some videos — skip silently
                continue

        if not comments:
            return {**state, "sentiment_scores": None, "sentiment_volume": None}

        # Step 3: score each comment with VADER
        analyzer = SentimentIntensityAnalyzer()
        pos = neu = neg = 0

        for comment in comments:
            scores = analyzer.polarity_scores(comment)
            compound = scores["compound"]
            if compound >= 0.05:
                pos += 1
            elif compound <= -0.05:
                neg += 1
            else:
                neu += 1

        total = len(comments)
        sentiment_scores = {
            "positive": round(pos / total, 4),
            "neutral": round(neu / total, 4),
            "negative": round(neg / total, 4),
        }

        return {
            **state,
            "sentiment_scores": sentiment_scores,
            "sentiment_volume": total,
        }

    except Exception:
        # Non-fatal: proceed without sentiment
        return {**state, "sentiment_scores": None, "sentiment_volume": None}


async def gemini_node(state: GraphState) -> GraphState:
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
    )
    try:
        sources = state.get("sources") or []
        sentiment_section = _format_sentiment_section(
            state.get("sentiment_scores"),
            state.get("sentiment_volume"),
        )

        if sources:
            prompt = _PROMPT.format(
                query=state["query"],
                sources=_format_sources(sources),
                sentiment_section=sentiment_section,
            )
        else:
            prompt = _PROMPT_NO_SOURCES.format(
                query=state["query"],
                sentiment_section=sentiment_section,
            )

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return {**state, "report_markdown": response.content, "error": None}
    except Exception as exc:
        return {**state, "report_markdown": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _build_graph():
    g = StateGraph(GraphState)
    g.add_node("tavily", tavily_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("gemini", gemini_node)
    g.set_entry_point("tavily")
    g.add_edge("tavily", "sentiment")
    g.add_edge("sentiment", "gemini")
    g.add_edge("gemini", END)
    return g.compile()


_graph = _build_graph()


async def run_graph(job_id: str, user_id: str, query: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(ResearchJob, uuid.UUID(job_id))
        if not job:
            return

        job.status = "running"
        await db.commit()

        try:
            state = await _graph.ainvoke(
                {
                    "job_id": job_id,
                    "user_id": user_id,
                    "query": query,
                    "sources": None,
                    "sentiment_scores": None,
                    "sentiment_volume": None,
                    "report_markdown": None,
                    "error": None,
                }
            )

            now = datetime.now(timezone.utc)
            if state["error"]:
                job.status = "failed"
                job.completed_at = now
            else:
                sources = state.get("sources") or []
                scores = state.get("sentiment_scores")
                volume = state.get("sentiment_volume")

                db.add(
                    Report(
                        id=uuid.uuid4(),
                        job_id=uuid.UUID(job_id),
                        user_id=uuid.UUID(user_id),
                        report_markdown=state["report_markdown"],
                        raw_context=sources,
                        source_count=len(sources),
                        sentiment_positive=scores["positive"] if scores else None,
                        sentiment_neutral=scores["neutral"] if scores else None,
                        sentiment_negative=scores["negative"] if scores else None,
                        youtube_comment_volume=volume,
                        overall_sentiment=_derive_overall_sentiment(scores),
                    )
                )
                job.status = "done"
                job.completed_at = now

            await db.commit()
        except Exception:
            await db.rollback()
            job = await db.get(ResearchJob, uuid.UUID(job_id))
            if job:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()
