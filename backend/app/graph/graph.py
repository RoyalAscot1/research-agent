import uuid
from datetime import datetime, timezone
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from tavily import TavilyClient

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.models import Report, ResearchJob


class GraphState(TypedDict):
    job_id: str
    user_id: str
    query: str
    sources: list[dict] | None
    report_markdown: str | None
    error: str | None


_PROMPT = """\
You are a research assistant. Write a thorough, well-structured markdown report answering the following query:

{query}

Use the web search results below as your primary sources. Cite them where relevant using [Source N] notation.

## Web Search Results

{sources}

---

Include: key findings, background context, and a brief summary. Use markdown headings and bullet points where appropriate.\
"""

_PROMPT_NO_SOURCES = """\
You are a research assistant. Write a thorough, well-structured markdown report answering the following query:

{query}

Include: key findings, background context, and a brief summary. Use markdown headings and bullet points where appropriate.\
"""


def _format_sources(sources: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sources, 1):
        title = s.get("title", "Untitled")
        url = s.get("url", "")
        content = s.get("content", "")
        lines.append(f"[Source {i}] {title}\nURL: {url}\n{content}")
    return "\n\n".join(lines)


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
    except Exception as exc:
        # Non-fatal: proceed without sources rather than failing the job
        return {**state, "sources": [], "error": None}


async def gemini_node(state: GraphState) -> GraphState:
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
    )
    try:
        sources = state.get("sources") or []
        if sources:
            prompt = _PROMPT.format(
                query=state["query"],
                sources=_format_sources(sources),
            )
        else:
            prompt = _PROMPT_NO_SOURCES.format(query=state["query"])

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return {**state, "report_markdown": response.content, "error": None}
    except Exception as exc:
        return {**state, "report_markdown": None, "error": str(exc)}


def _build_graph():
    g = StateGraph(GraphState)
    g.add_node("tavily", tavily_node)
    g.add_node("gemini", gemini_node)
    g.set_entry_point("tavily")
    g.add_edge("tavily", "gemini")
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
                db.add(
                    Report(
                        id=uuid.uuid4(),
                        job_id=uuid.UUID(job_id),
                        user_id=uuid.UUID(user_id),
                        report_markdown=state["report_markdown"],
                        raw_context=sources,
                        source_count=len(sources),
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
