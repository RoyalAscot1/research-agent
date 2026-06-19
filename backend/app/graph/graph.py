import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import TypedDict

from googleapiclient.discovery import build
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langfuse import observe
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from tavily import TavilyClient
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import bind_job_id, clear_log_context, get_logger
from app.models.models import Report, ResearchJob

log = get_logger(__name__)

# Hard ceiling on any single Gemini request. Without it a hung LLM call leaves
# the LangGraph run (and its `research_jobs` row) stuck in `running` forever —
# the frontend poll cap only surfaces the symptom.
#
# Enforced with `asyncio.wait_for` around each `ainvoke`, NOT the constructor's
# `timeout=` kwarg: the current (deprecated) `google.generativeai` client silently
# ignores that field — verified that a 0.001s constructor timeout still completed
# the request. `wait_for` is provider-agnostic and guaranteed to raise. On timeout
# the per-node try/except blocks turn the error into a fail-soft (planner/coverage)
# or a `failed` job (synthesizer), instead of hanging.
_LLM_REQUEST_TIMEOUT_SECONDS = 60


class ResearchPlan(BaseModel):
    search_queries: list[str] = Field(
        description="1 to 3 focused search queries to send to Tavily. Decompose the user query into specific, searchable sub-questions.",
        min_length=1,
        max_length=3,
    )
    run_sentiment: bool = Field(
        description="True if YouTube public sentiment is relevant to this query (opinion, product, event, public figure). False for factual, technical, or academic queries."
    )
    youtube_search_query: str = Field(
        description="A concise, specific YouTube search query to find popular videos with many comments on the topic. Optimise for discoverability — use the most commonly searched form of the topic, not the user's exact wording."
    )


class GraphState(TypedDict):
    job_id: str
    user_id: str
    query: str
    search_queries: list[str] | None
    run_sentiment: bool | None
    youtube_search_query: str | None
    sources: list[dict] | None
    sentiment_scores: dict | None  # {"positive": float, "neutral": float, "negative": float}
    sentiment_volume: int | None  # number of comments analysed
    report_markdown: str | None
    error: str | None
    tavily_call_count: int  # running total — checked against the 5-call hard cap
    iteration_count: int  # running total — checked against the 3-iteration cap
    coverage_sufficient: bool | None  # set by the coverage-check step each pass through the loop
    tried_queries: list[str]  # cumulative history of every Tavily query attempted — lets
    # the coverage check avoid proposing near-duplicates of past rounds


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


def _content_to_str(content) -> str:
    """Normalise a LangChain message's `content` to a string.

    `content` is a `str` for most responses but can be a list of parts; join
    the list into a single string so callers can treat it uniformly.
    """
    if isinstance(content, list):
        return "".join(part if isinstance(part, str) else str(part) for part in content)
    return content


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


_FOLLOWUP_PROMPT = """\
You are a research assistant helping a user dig deeper into a topic.

## Original Query
{query}

## Research Report
{report_markdown}

## Sources
{sources}

{prior_turns}## Follow-up Question
{question}

Answer the follow-up question thoroughly, referencing the report and sources where relevant. \
Use markdown formatting. Cite sources as [Source N] where applicable.\
"""


@observe()
async def call_gemini_followup(
    query: str,
    report_markdown: str,
    sources: list[dict],
    prior_turns: list[dict],
    question: str,
) -> str:
    """Call Gemini to answer a follow-up question given the original report context."""
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
    )

    prior_block = ""
    if prior_turns:
        lines = ["## Prior Conversation\n"]
        for turn in prior_turns:
            lines.append(f"**Q{turn['turn_number']}:** {turn['question']}\n")
            lines.append(f"**A{turn['turn_number']}:** {turn['answer']}\n\n")
        prior_block = "".join(lines)

    prompt = _FOLLOWUP_PROMPT.format(
        query=query,
        report_markdown=report_markdown,
        sources=_format_sources(sources) if sources else "No sources available.",
        prior_turns=prior_block,
        question=question,
    )

    response = await asyncio.wait_for(
        llm.ainvoke([HumanMessage(content=prompt)]),
        timeout=_LLM_REQUEST_TIMEOUT_SECONDS,
    )
    return _content_to_str(response.content)


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

_PLANNER_PROMPT = """\
You are a research planner. Given a user query, decide how to research it effectively.

## User Query
{query}

## Instructions
1. Decompose the query into focused, specific search queries for a web search engine.
   - Default to 1–2 sub-questions. Use 3 only if the query genuinely requires multiple distinct angles.
   - Prefer precision over breadth — a narrow query returns better results than a broad one.
   - If the query is already specific, a single search query is fine.
   - Do not repeat the same query with minor wording changes.

2. Decide whether YouTube public sentiment is relevant.
   - Set run_sentiment=true for: product reviews, public figures, movies/TV/music, political topics, consumer brands, events with public opinion.
   - Set run_sentiment=false for: technical documentation, academic topics, factual lookups, coding questions, scientific concepts.\
"""


@observe()
async def planner_node(state: GraphState) -> GraphState:
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
    ).with_structured_output(ResearchPlan)

    prompt = _PLANNER_PROMPT.format(query=state["query"])

    try:
        plan: ResearchPlan = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=_LLM_REQUEST_TIMEOUT_SECONDS,
        )
        log.info(
            "planner.complete",
            search_queries=len(plan.search_queries),
            run_sentiment=plan.run_sentiment,
        )
        return {
            **state,
            "search_queries": plan.search_queries,
            "run_sentiment": plan.run_sentiment,
            "youtube_search_query": plan.youtube_search_query,
        }
    except Exception:
        # Fall back to using the raw query as a single search and skipping sentiment
        log.warning("planner.fallback", exc_info=True)
        return {
            **state,
            "search_queries": [state["query"]],
            "run_sentiment": False,
            "youtube_search_query": state["query"],
        }


@observe()
async def researcher_node(state: GraphState) -> GraphState:
    """
    Calls Tavily for each planned sub-query, merging and deduplicating results by URL.

    Accumulates across re-plan iterations (starts from any sources already in state)
    and respects a hard cap of 5 total Tavily calls per graph run, tracked via
    `tavily_call_count`.
    """
    try:
        client = TavilyClient(api_key=settings.tavily_api_key)
        search_queries = state.get("search_queries") or [state["query"]]

        # Start from whatever was gathered in prior iterations rather than overwriting
        merged: list[dict] = list(state.get("sources") or [])
        seen_urls: set[str] = {s["url"] for s in merged if s.get("url")}
        call_count = state.get("tavily_call_count", 0)
        tried_queries: list[str] = list(state.get("tried_queries") or [])

        for q in search_queries:
            if call_count >= 5:
                break  # hard cap reached — stop issuing new Tavily searches

            call_count += 1
            tried_queries.append(q)
            try:
                response = client.search(
                    query=q,
                    search_depth="basic",
                    max_results=8,
                    include_answer=False,
                )
                for result in response.get("results", []):
                    url = result.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        merged.append(result)
            except Exception as exc:
                # this sub-query failed — keep going with what we have
                log.warning("tavily.search_failed", query=q, error=str(exc))

        log.info(
            "researcher.complete",
            sources=len(merged),
            tavily_calls=call_count,
            iteration=state.get("iteration_count", 0) + 1,
        )
        return {
            **state,
            "sources": merged,
            "tavily_call_count": call_count,
            "tried_queries": tried_queries,
            "iteration_count": state.get("iteration_count", 0) + 1,
            "error": None,
        }
    except Exception:
        # Non-fatal: proceed without sources — no queries were actually attempted
        # (the failure happened before the loop, e.g. TavilyClient construction)
        log.warning("researcher.failed", exc_info=True)
        return {
            **state,
            "sources": state.get("sources") or [],
            "iteration_count": state.get("iteration_count", 0) + 1,
            "error": None,
        }


class CoverageAssessment(BaseModel):
    sufficient: bool = Field(
        description="True if the gathered sources are enough to write a thorough, accurate report answering the query. False if there are clear, important gaps."
    )
    additional_queries: list[str] = Field(
        default_factory=list,
        description="If sufficient is False, 1-2 new focused search queries that would fill the gaps. Must not duplicate or closely overlap queries already tried. Empty if sufficient is True.",
        max_length=2,
    )


_COVERAGE_PROMPT = """\
You are evaluating whether enough web research has been gathered to answer a user's query thoroughly.

## User Query
{query}

## Search Queries Already Tried
{tried_queries}

## Sources Gathered So Far
{sources}

## Instructions
Decide whether this research is sufficient to write a thorough, accurate report answering the query.
- Mark sufficient=true if the sources adequately cover the topic's key angles.
- Mark sufficient=false only if there are clear, important gaps — and propose 1-2 focused search queries that would fill them, distinct from the queries already tried.
- Default to sufficient=true when in doubt — more research has diminishing returns and real cost.\
"""


@observe()
async def coverage_check_node(state: GraphState) -> GraphState:
    """
    Decides whether the accumulated research is good enough, or whether the
    researcher should run another round with new sub-queries.

    The hard caps (3 iterations, 5 Tavily calls) are enforced here as a backstop —
    once either is reached, this short-circuits to sufficient=True without an LLM
    call, regardless of what the model might otherwise suggest.
    """
    iteration_count = state.get("iteration_count", 0)
    tavily_call_count = state.get("tavily_call_count", 0)

    if iteration_count >= 3 or tavily_call_count >= 5:
        log.info(
            "coverage.decision",
            sufficient=True,
            reason="hard_cap",
            iteration=iteration_count,
            tavily_calls=tavily_call_count,
        )
        return {**state, "coverage_sufficient": True}

    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
    ).with_structured_output(CoverageAssessment)

    sources = state.get("sources") or []
    # Use the cumulative history, not `search_queries` — that field gets overwritten
    # each pass (by the planner initially, then by this node), so it only ever holds
    # the most recent batch and would let the model propose near-duplicates of
    # earlier rounds it can no longer see.
    tried_queries = state.get("tried_queries") or []

    prompt = _COVERAGE_PROMPT.format(
        query=state["query"],
        tried_queries="\n".join(f"- {q}" for q in tried_queries) or "(none yet)",
        sources=_format_sources(sources) if sources else "No sources gathered yet.",
    )

    try:
        assessment: CoverageAssessment = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=_LLM_REQUEST_TIMEOUT_SECONDS,
        )
        if assessment.sufficient or not assessment.additional_queries:
            log.info(
                "coverage.decision", sufficient=True, reason="model", iteration=iteration_count
            )
            return {**state, "coverage_sufficient": True}
        log.info(
            "coverage.decision",
            sufficient=False,
            reason="model",
            iteration=iteration_count,
            additional_queries=len(assessment.additional_queries[:2]),
        )
        return {
            **state,
            "coverage_sufficient": False,
            "search_queries": assessment.additional_queries[:2],
        }
    except Exception:
        # Fail safe — an unreliable signal shouldn't keep the loop spinning
        log.warning("coverage.check_failed", exc_info=True)
        return {**state, "coverage_sufficient": True}


@observe()
async def sentiment_node(state: GraphState) -> GraphState:
    """
    Fetches top YouTube comments for the query and scores them with VADER.
    Non-fatal — failures return empty sentiment and the graph continues.
    """
    try:
        youtube = build("youtube", "v3", developerKey=settings.youtube_api_key)

        # Step 1: search for top 5 relevant videos
        yt_query = state.get("youtube_search_query") or state["query"]
        search_response = (
            youtube.search()
            .list(
                q=yt_query,
                part="id",
                type="video",
                maxResults=5,
                order="relevance",
                videoCaption="any",
            )
            .execute()
        )

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
                comments_response = (
                    youtube.commentThreads()
                    .list(
                        part="snippet",
                        videoId=video_id,
                        maxResults=20,
                        order="relevance",
                        textFormat="plainText",
                    )
                    .execute()
                )
                for item in comments_response.get("items", []):
                    text = item["snippet"]["topLevelComment"]["snippet"].get("textDisplay", "")
                    if text:
                        comments.append(text)
            except Exception as exc:
                # Comments may be disabled on some videos — expected, so debug not warning
                log.debug("youtube.comments_skipped", video_id=video_id, error=str(exc))
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

        log.info("sentiment.complete", comments_analysed=total, videos=len(video_ids))
        return {
            **state,
            "sentiment_scores": sentiment_scores,
            "sentiment_volume": total,
        }

    except Exception:
        # Non-fatal: proceed without sentiment
        log.warning("sentiment.failed", exc_info=True)
        return {**state, "sentiment_scores": None, "sentiment_volume": None}


@observe()
async def synthesizer_node(state: GraphState) -> GraphState:
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

        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=_LLM_REQUEST_TIMEOUT_SECONDS,
        )
        # content can be a str or a list of parts depending on the response;
        # normalise to a string so the emptiness check below is reliable.
        content = _content_to_str(response.content)
        if not content or not content.strip():
            # A blank report with no exception would otherwise be persisted and
            # marked `done` (run_graph treats a falsy error as success), leaving
            # the user with an empty report. Flip it to a failure instead.
            log.warning("synthesizer.empty_report")
            return {
                **state,
                "report_markdown": None,
                "error": "Synthesizer produced an empty report",
            }
        log.info("synthesizer.complete", report_chars=len(content))
        return {**state, "report_markdown": content, "error": None}
    except Exception as exc:
        # str(exc) is empty for some exceptions (notably asyncio.TimeoutError),
        # and run_graph treats a falsy error as success — fall back to the class
        # name so a timeout reliably flips the job to `failed` instead of
        # persisting a null report.
        log.exception("synthesizer.failed")
        return {**state, "report_markdown": None, "error": str(exc) or type(exc).__name__}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _route_after_coverage(state: GraphState) -> str:
    """
    Routes back into the research loop if coverage is insufficient and both hard
    caps still allow another pass; otherwise proceeds to sentiment/synthesizer
    based on the planner's run_sentiment decision — same as before the loop existed.
    """
    coverage_sufficient = state.get("coverage_sufficient")
    iteration_count = state.get("iteration_count", 0)
    tavily_call_count = state.get("tavily_call_count", 0)

    if coverage_sufficient is False and iteration_count < 3 and tavily_call_count < 5:
        return "researcher"

    return "sentiment" if state.get("run_sentiment") else "synthesizer"


def _build_graph():
    g = StateGraph(GraphState)
    g.add_node("planner", planner_node)
    g.add_node("researcher", researcher_node)
    g.add_node("coverage_check", coverage_check_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("synthesizer", synthesizer_node)
    g.set_entry_point("planner")
    g.add_edge("planner", "researcher")
    g.add_edge("researcher", "coverage_check")
    g.add_conditional_edges(
        "coverage_check",
        _route_after_coverage,
        {"researcher": "researcher", "sentiment": "sentiment", "synthesizer": "synthesizer"},
    )
    g.add_edge("sentiment", "synthesizer")
    g.add_edge("synthesizer", END)
    return g.compile()


_graph = _build_graph()


@observe()
async def run_graph(job_id: str, user_id: str, query: str) -> None:
    bind_job_id(job_id)
    start = time.perf_counter()
    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(ResearchJob, uuid.UUID(job_id))
            if not job:
                log.warning("job.not_found")
                return

            job.status = "running"
            await db.commit()
            log.info("job.started", user_id=user_id, query=query)

            try:
                state = await _graph.ainvoke(
                    {
                        "job_id": job_id,
                        "user_id": user_id,
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
                )

                now = datetime.now(timezone.utc)
                duration_s = round(time.perf_counter() - start, 2)
                if state["error"]:
                    job.status = "failed"
                    job.completed_at = now
                    log.warning("job.failed", error=state["error"], duration_s=duration_s)
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
                    log.info(
                        "job.done",
                        duration_s=duration_s,
                        source_count=len(sources),
                        has_sentiment=scores is not None,
                    )

                await db.commit()
            except Exception:
                log.exception("job.error", duration_s=round(time.perf_counter() - start, 2))
                await db.rollback()
                job = await db.get(ResearchJob, uuid.UUID(job_id))
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    await db.commit()
    finally:
        clear_log_context()
