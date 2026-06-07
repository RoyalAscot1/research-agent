# Lens — AI Research & Sentiment App: Build Summary

## What the app does

Lens is a web app that lets users ask natural language research questions and receive AI-generated reports combining factual web research with public sentiment data from YouTube comments. Users can follow up on each report with up to 5 questions before starting a new query. All queries and reports are saved to a per-user history.

---

## User journey

1. **Sign in** — Google OAuth via Clerk. Lands directly on the prompt screen.
2. **Prompt screen** — single search bar. User types a query and hits Run. No source configuration — the agent decides automatically.
3. **Progress screen** — live step indicator while the agent runs (Planning → Researching → Sentiment → Writing). Typical run time: 15–40 seconds. User can navigate away; job continues in background.
4. **Chat screen** — the finished report loads as a styled card at the top:
   - Three sentiment score cards (Positive %, Neutral %, Negative %) derived from YouTube comments — hidden if sentiment data is unavailable
   - AI-written summary (3–4 paragraphs combining facts and sentiment)
   - Source cards showing title, domain, and publish date — hidden if no sources available
   - Below the report: a chat input for follow-up questions
   - Up to 5 follow-up questions allowed. After 5, the input is replaced with a "Follow-up limit reached (5/5)" message.
5. **History screen** — list of all past queries with sentiment badge (Positive / Mixed / Negative), date, and run time. Click any row to reopen the full report and follow-up thread (read-only). Delete individual entries or clear all.

---

## Architecture overview

The app has two separately deployed services:

- **Next.js frontend** — deployed to Vercel
- **FastAPI backend** — deployed to Render as a Docker container

Both share a single Postgres database.

---

## Tech stack

### Frontend
| Tool | Purpose |
|------|---------|
| Next.js 16 (App Router) | React framework. Routes: `/` (prompt), `/history`, `/chat/[id]` |
| Tailwind CSS v4 | Primary styling system — all layout, color tokens, utilities |
| framer-motion | Animations — entrance, state transitions, spring-physics interactions |
| @base-ui/react | Headless UI primitives (the runtime underneath shadcn-generated components) |
| shadcn/ui (CLI) | Component generator — copies component source into `components/ui/`. Used selectively, not as a full component library |
| lucide-react | Icon library |
| Clerk | Auth — Google OAuth, session management, hosted sign-in UI |

### Backend
| Tool | Purpose |
|------|---------|
| FastAPI (async) | REST API — all endpoints |
| LangGraph | Agent orchestration — planner, researcher, sentiment, and synthesizer nodes |
| Gemini (`langchain-google-genai`) | LLM for planning, synthesis, follow-up answers. Use `gemini-3.1-flash-lite-preview` for most nodes, `gemini-3.1-flash-preview` for the synthesizer if quality warrants it |
| Tavily | LLM-optimised web search. Called by the researcher node |
| google-api-python-client | YouTube Data API v3 — searches for relevant videos and fetches top comments for sentiment analysis |
| VADER (nltk) | Sentiment scoring for YouTube comments. Classifies positive/neutral/negative |
| SQLAlchemy (async) + asyncpg | Python ORM for Postgres |
| Alembic | Database migrations (owns all schema changes) |
| Prisma | TypeScript ORM for Next.js side (history + app queries) — uses same Postgres DB |

### Storage
| Tool | Purpose |
|------|---------|
| Postgres | Primary database — all structured data (see schema below) |
| pgvector (deferred to v2) | Vector search via Postgres extension — will store embedded research chunks in a `document_chunks` table, filtered by `report_id`, for semantic follow-up retrieval. No separate service; runs on the existing Neon instance |
| Redis (optional, add post-v1) | Job status cache, sentiment cache (TTL 6–24h), rate limit counters |

### Infrastructure
| Tool | Purpose |
|------|---------|
| Docker | Packages FastAPI backend |
| GitHub Actions | CI/CD — runs pytest + lint on push, triggers Render deploy |
| Render | Hosts FastAPI Docker container |
| Vercel | Hosts Next.js frontend — auto-deploys on push |
| Neon or Supabase | Managed Postgres |

### Dev tools
| Tool | Purpose |
|------|---------|
| pytest | Backend tests |
| Ruff | Python linting and formatting |
| Bruno (or FastAPI /docs) | API testing during development |

---

## Database schema (Postgres)

```sql
-- users table synced from Clerk via webhook (Clerk owns auth, Postgres owns app data)
users (id, clerk_user_id, email, created_at)

-- One row per query run
research_jobs (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  query TEXT,
  status TEXT,        -- pending | running | done | failed
  -- current_step TEXT  -- (deferred to v2) written by graph nodes for progress display
  created_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
)

-- Finished report — linked to job
reports (
  id UUID PRIMARY KEY,
  job_id UUID REFERENCES research_jobs(id),
  user_id UUID REFERENCES users(id),
  report_markdown TEXT,          -- the AI-written report
  raw_context JSONB,             -- frozen research payload for follow-ups
  sentiment_positive FLOAT,
  sentiment_neutral FLOAT,
  sentiment_negative FLOAT,
  youtube_comment_volume INT,
  source_count INT,
  overall_sentiment TEXT,        -- Positive | Mixed | Negative
  created_at TIMESTAMPTZ
)

-- Follow-up Q&A pairs (max 5 per report)
follow_ups (
  id UUID PRIMARY KEY,
  report_id UUID REFERENCES reports(id),
  user_id UUID REFERENCES users(id),
  question TEXT,
  answer TEXT,
  turn_number INT,               -- 1–5
  created_at TIMESTAMPTZ
)
```

---

## API endpoints (FastAPI)

```
POST   /queries                        Start a new research job
GET    /jobs/{job_id}/status           Poll job status (returns status, report_id when done; current_step field deferred to v2)
GET    /reports/{report_id}            Load a finished report
POST   /reports/{report_id}/followup   Submit a follow-up question (max 5)
GET    /history                        List all reports for the authenticated user
DELETE /history/{report_id}           Delete a report
DELETE /history                        Clear all history for the user
```

All endpoints with a UUID path parameter (`/jobs/{job_id}/status`, `/reports/{report_id}`, `/reports/{report_id}/followup`, `DELETE /history/{report_id}`) return **404** — not 500 — for a malformed or non-existent ID. The malformed case is guarded with `try/except ValueError` around `uuid.UUID(...)` before the DB lookup.

---

## LangGraph graph

### Live (agent with re-plan loop — steps 1–16, all done)

The graph is a true agent with conditional edges and a working re-plan loop:

```
Planner → Researcher → Coverage check ─┬─→ Researcher        (loop: insufficient & under both caps)
                                         ├─→ Sentiment         (run_sentiment=True)
                                         └─→ Synthesizer       (run_sentiment=False)
                                                ↓
                                            Sentiment → Synthesizer → END
```

**Planner** — Gemini structured output (`ResearchPlan`) decides query decomposition (1–3 Tavily sub-queries, biased toward 1–2), whether to run sentiment (`run_sentiment: bool`), and a dedicated YouTube search query. Falls back to raw query + `run_sentiment=False` if structured output fails.

**Researcher** (renamed from the old `tavily` node) — calls Tavily for web results (basic depth, 1 credit/call), running sub-queries back-to-back with no artificial pacing — Tavily is built for rapid sequential agent calls, and the hard cap (not a delay) is what bounds cost. Merges and deduplicates results by URL, **accumulating across re-plan iterations** rather than overwriting. Tracks `tavily_call_count` and stops issuing new searches once it hits the hard cap of 5 total Tavily calls per run. Passes merged raw sources straight to the synthesizer — chunking/embedding into `document_chunks` via pgvector is deferred to v2 (see Deferred polish).

**Coverage check** (new) — Gemini structured output (`CoverageAssessment`) judges whether the gathered sources are sufficient to write a thorough report, or proposes up to 2 new sub-queries to fill gaps. Reads from `tried_queries` — a *cumulative* history of every query attempted across all rounds (deliberately not `search_queries`, which rotates each pass and would let the model propose near-duplicates of earlier rounds it can no longer see). Short-circuits to `sufficient=True` **without an LLM call** once `iteration_count >= 3` or `tavily_call_count >= 5` — the hard caps are backstops that override the model's opinion, not just guidance to it. Fails safe to `sufficient=True` on any error so a flaky signal can't keep the loop spinning.

**Sentiment** — unchanged from the original implementation. Gated by the planner's `run_sentiment` boolean; the conditional edge now sits after the coverage-check/loop rather than directly after research, but the branch logic is identical.

**Synthesizer** — assembles a prompt from web sources (formatted with `[Source N]` citations) + sentiment context block + output format instruction, same as before.

**Validated live** (see commit history / testing notes) against:
- A narrow query with a false premise ("Fed FOMC June 2026 meeting" — no such meeting occurred): the loop correctly recognized sparse initial results as a gap, re-planned with genuinely diverse follow-up queries (after the `tried_queries` fix), and converged on the accurate "no meeting occurred" answer — in one run resolving in 2 rounds via genuine LLM sufficiency judgment, in another hitting both hard caps simultaneously at round 3 (a clean confirmation that the two independent backstops converge correctly).
- A broad query ("state of AI regulation worldwide"): the planner produced 2 well-targeted broad sub-queries, and coverage check judged them sufficient immediately (1 round, well under both caps) — confirming the loop doesn't pad runs with unnecessary searches when the initial plan is already comprehensive.
- Both runs also exercised the `run_sentiment` conditional correctly in opposite directions (False for the Fed query, True for the AI regulation query, with a sensibly-phrased `youtube_search_query`).

**Cost/latency bounds** — worst case is 9 node executions (1 planner + 3 researcher + 3 coverage_check + 1 sentiment + 1 synthesizer): 5 Gemini calls (planner, up to 3 coverage checks, synthesizer) and up to 5 Tavily calls, both hard-capped. Typical queries resolve in 1–2 research rounds based on live testing.

---

## Follow-up implementation

Follow-ups do NOT re-run the full agent graph. They re-use existing research context.

**v1 (steps 12–13) — stateless re-synthesis from `raw_context`:**
1. Verify Clerk JWT + ownership check (403 if report belongs to another user)
2. Count existing `follow_ups` rows — reject with 429 if ≥ 5
3. Load `raw_context` from Postgres (Tavily sources stored as JSONB)
4. Load all prior follow-up Q&A pairs ordered by `turn_number` for conversation history
5. Pass original query + report_markdown + sources + conversation history + new question to `call_gemini_followup()` in `graph.py`
6. Save the answer to `follow_ups` table with `turn_number = existing_count + 1`
7. Return `{answer, turn_number}` — typically responds in ~3 seconds

**v2 (deferred, alongside pgvector) — semantic retrieval from `document_chunks`:**
1–2 same as above
3. Query `document_chunks` WHERE `report_id = $1` ORDER BY embedding similarity to the follow-up question LIMIT k
4. Pass retrieved chunks + conversation history + new question to Gemini
5–7 same as above

Enforce a hard limit of 5 follow-ups per report at the API level. After 5, return a 429 with a message prompting a new query. The follow-up prompt instructs Gemini: "Answer using only the research above. If the research doesn't cover this, say so clearly. Do not invent new facts."

---

## Sentiment context block format (fed to synthesizer)

```
## Public Sentiment (YouTube Comments)

Positive: X% | Neutral: Y% | Negative: Z%
Comments analysed: N

Incorporate this sentiment signal where relevant — note whether public opinion is broadly positive, negative, or divided on the topic.
```

---

## Key implementation notes

- **Two ORMs, one DB**: Prisma (Next.js/TypeScript) and SQLAlchemy (FastAPI/Python) both point at the same Postgres database. Alembic owns all migrations — configure Prisma to use the existing schema, not manage its own.
- **YouTube API quota**: YouTube Data API v3 gives 10,000 units/day free. A search costs 100 units; a comment list page costs 1 unit. Fetch top 5 videos, top 20 comments each — well within quota for dev.
- **pgvector chunk retrieval (v2)**: Once `document_chunks` lands, query it with `WHERE report_id = $1 ORDER BY embedding <=> $2 LIMIT k`. Always filter by `report_id` — never bleed chunks from other reports into a follow-up answer. Cascade deletes handle cleanup when a report is deleted.
- **Sentiment cache (Redis, post-v1)**: Key: `sentiment:{sha256(query)}`. TTL: 6 hours. Skip YouTube sentiment entirely if cache hit.
- **Progress reporting (deferred to v2)**: Graph nodes will write a human-readable `current_step` string to the `research_jobs` row as they progress (e.g. "Planning", "Searching: Tesla Q1 earnings", "Coverage insufficient — refining search", "Analysing sentiment", "Writing report"), and `GET /jobs/{job_id}/status` will return it for the frontend to display as a status line. Requires a new `current_step` column + migration — see Deferred polish. Until then, the prompt screen's progress indicator stays generic (status only, no per-step detail).
- **VADER upgrade path**: Start with VADER for speed. Upgrade to `cardiffnlp/twitter-roberta-base-sentiment` if sarcasm/slang causes poor results.
- **Report overall sentiment**: Derive from scores — Positive if positive% > 55%, Negative if negative% > 30%, else Mixed.
- **Clerk auth**: Use `@clerk/nextjs`. Configure `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` as environment variables on Vercel. Sync Clerk users into the `users` table via a Clerk webhook on `user.created`.
- **CORS**: Configure FastAPI CORS middleware to allow requests from the Vercel frontend domain.
- **Environment variables**: Never hardcode API keys. Use Render environment variables for the backend (GEMINI_API_KEY, TAVILY_API_KEY, YOUTUBE_API_KEY, DATABASE_URL). Use Vercel environment variables for the frontend (NEXT_PUBLIC_API_URL, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY).

---

## Build order (recommended)

1. Postgres schema + Alembic migrations (done)
2. FastAPI skeleton with all endpoints returning stubs (done)
3. Clerk — auth flow end to end (done)
4. LangGraph graph — single node (Gemini only, no tools) to validate wiring (done)
5. Add Tavily researcher node (done)
6. Add YouTube comments + VADER sentiment node (done)
7. Implement real FastAPI report + history endpoints (stubs → real DB reads, add auth guards) (done)
8. Next.js frontend — prompt screen + progress polling (done)
9. Next.js frontend — chat screen (report card display, no follow-ups yet) (done)
10. Synthesizer prompt iteration — run 20+ real queries in the browser, refine until quality is consistent (skipped for now — output quality acceptable)
11. Next.js frontend — history screen (done)
12. Follow-up endpoint (done)
13. Next.js frontend — follow-up chat UI (done)
14. Configure Langfuse — set up tracing before adding agentic nodes so every graph execution is observable from day one (done)
15. Add Planner node — LLM decides how to decompose the query and whether to run sentiment (done)
16. Add Researcher node — re-plan loop (max 3 iterations, 5 Tavily calls hard cap) (done)
17. Docker + GitHub Actions + Render deploy
18. Redis caching (post-v1)
19. pgvector + `document_chunks` — semantic chunk storage and retrieval for follow-ups (post-v1)

---

## Production concerns

- **Langfuse** — observability for LangGraph; traces of every graph execution showing node inputs/outputs and latency per node. Live via `@observe` decorators on all graph nodes and `call_gemini_followup`.
- **Structured logging** — replace print statements with `structlog` JSON logs; every graph node should log inputs, outputs, latency, and cache hits, with a request ID threaded through for end-to-end tracing.
- **Sentry** — two-line integration for real error tracking and alerting.
- **Task queue** — `BackgroundTasks` runs jobs in the same process as the web server; replace with ARQ (async, fits the codebase) or Celery + Redis for any real load.
- **Rate limiting** — no per-user query limits; add with `slowapi` on FastAPI.
- **Cost guardrails** — no cap on Tavily credits per user; track usage and throttle at a threshold before shipping.
- **Testing** — no test suite; pytest covering the graph nodes and critical API endpoints is the gap between personal project and production system.
- **CI/CD** — GitHub Actions running lint + pytest on push and deploying to Render on merge to main; table stakes for a production-aware project (already in build order step 17).
- **pgvector on Neon (deferred to v2)** — enable via `CREATE EXTENSION vector;` migration when `document_chunks` lands. Gemini `text-embedding-004` produces 768-dimension vectors. Index with `ivfflat` or `hnsw` once chunk volume grows.
- **FAISS** — in-process vector search library, faster than pgvector at very large scale but requires manual persistence management; pgvector is the right call for this app.
- **Elasticsearch** — hybrid BM25 + vector search in one query; worth knowing for interviews as the production alternative to pgvector when exact keyword matching matters alongside semantic search.

---

## Deferred polish (v2)

- **pgvector + `document_chunks`** — semantic chunk storage and retrieval, originally scoped into step 16. Moved to v2 so the Researcher node (re-plan loop, hard caps — now shipped and validated live) could land without taking on embedding-pipeline and schema-migration risk at the same time. Until this lands, the researcher passes merged raw Tavily results straight to the synthesizer and follow-ups keep using `raw_context` (see "v2 — semantic retrieval" flow above for the target design). Target schema:
  ```sql
  document_chunks (
    id UUID PRIMARY KEY,
    report_id UUID REFERENCES reports(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    content TEXT,
    embedding vector(768),         -- Gemini text-embedding-004 output dimension
    created_at TIMESTAMPTZ
  )
  ```
- **Progress indicator redesign + `current_step` backend wiring** — both the `current_step` column/migration/status-endpoint plumbing and the frontend redesign (replacing the spinning disk with a live feed of step strings — "Planning", "Searching: [term]", "Coverage insufficient — refining", "Writing report") are deferred to v2 as one unit. The Researcher node (step 16, now live) shipped without per-step progress reporting as planned; status polling remains coarse-grained (`pending`/`running`/`done`/`failed`) until this lands.
- **Light/dark mode toggle** — the design is dark-first; a toggle can be added post-v1 once core functionality is complete.
