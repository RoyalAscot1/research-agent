import uuid
from datetime import datetime, timezone
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.models import Report, ResearchJob


class GraphState(TypedDict):
    job_id: str
    user_id: str
    query: str
    report_markdown: str | None
    error: str | None


_PROMPT = """\
You are a research assistant. Write a thorough, well-structured markdown report answering the following query:

{query}

Include: key findings, background context, and a brief summary. Use markdown headings and bullet points where appropriate.\
"""


async def gemini_node(state: GraphState) -> GraphState:
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
    )
    try:
        response = await llm.ainvoke([HumanMessage(content=_PROMPT.format(query=state["query"]))])
        return {**state, "report_markdown": response.content, "error": None}
    except Exception as exc:
        return {**state, "report_markdown": None, "error": str(exc)}


def _build_graph():
    g = StateGraph(GraphState)
    g.add_node("gemini", gemini_node)
    g.set_entry_point("gemini")
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

        state = await _graph.ainvoke(
            {"job_id": job_id, "user_id": user_id, "query": query, "report_markdown": None, "error": None}
        )

        now = datetime.now(timezone.utc)
        if state["error"]:
            job.status = "failed"
            job.completed_at = now
        else:
            db.add(
                Report(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(job_id),
                    user_id=uuid.UUID(user_id),
                    report_markdown=state["report_markdown"],
                )
            )
            job.status = "done"
            job.completed_at = now

        await db.commit()
