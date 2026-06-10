# Lens — Claude Working Guide

## What this is
Lens is a full-stack AI research and sentiment app. Users submit natural language queries and receive AI-generated reports combining web research with YouTube comment sentiment data. See `app_summary.md` for the full product spec.

## Repo structure
```
backend/   FastAPI + LangGraph (Python)
frontend/  Next.js 16 (TypeScript)
```

## Current state
- Backend: FastAPI with Clerk JWT auth wired up; `POST /queries` creates a `research_jobs` row and fires a LangGraph background task; all report/history endpoints live — `GET /jobs/{job_id}/status` returns `report_id` when done, `GET /reports/{report_id}` returns full report + follow_ups, `GET /history` returns paginated list, `DELETE /history/{report_id}` and `DELETE /history` both implemented. All endpoints auth guarded.
- Follow-up endpoint live (`POST /reports/{report_id}/followup`): auth guarded, 5-follow-up cap (429 on breach), loads `raw_context` + prior Q&A turns, calls Gemini via `call_gemini_followup()` in `graph.py`, persists `FollowUp` row. All UUID-path endpoints — `/reports/{id}`, `/followup`, `GET /jobs/{job_id}/status`, and `DELETE /history/{report_id}` — return 404 (not 500) for malformed or non-existent UUIDs (malformed IDs are caught with a `try/except ValueError` around `uuid.UUID(...)` before the DB lookup).
- Frontend: Next.js 16, Tailwind, shadcn/ui, Prisma v7, framer-motion. All three screens live: `/` (prompt), `/history`, `/chat/[id]`.
- Prompt screen (`app/page.tsx`): Client Component with four states — loading, sign-in gate, researching (polling), idle/error. Submits query via `POST /queries`, polls `GET /jobs/{job_id}/status` every 2s, redirects to `/chat/{report_id}` on completion. Clerk token fetched fresh each poll tick via `getToken()`. Floating History + Sign Out buttons top-right when signed in.
- Chat screen (`app/chat/[id]/page.tsx`): Client Component. Uses `React.use(params)` to unwrap the route param. Three render states: loading, error, loaded. Fetches `GET /reports/{id}` on mount using `resolveToken`. Layout: transparent sticky nav (Lens wordmark + History link left, copy/download buttons + completion time right); query title; three sentiment tiles (conditionally hidden if sentiment is null); markdown report via `react-markdown` + `remark-gfm` with custom dark-themed component overrides; source cards linking to Tavily URLs; follow-up Q&A thread + chat input below the report. All sections are conditional — degrades gracefully with no sentiment or no sources. Follow-up state: `followUps` seeded from `report.follow_ups` on load, `question`, `submitting`, `limitReached` (set on load if already at 5, after 5th successful submit, or on 429), `followUpError` (shown on non-429 errors, cleared on next submit). Optimistic UI — question bubble appears immediately, spinner while answer is in flight, answer fades in via `AnimatePresence mode="wait"`. `suggested_followups` field removed from API response and `ReportData` type (DB column kept, always null, never populated).
- History screen (`app/history/page.tsx`): Client Component. Three render states: loading, error, loaded. Fetches `GET /history` on mount. Lists reports as glassmorphism cards — query text, colour-coded sentiment badge, date. Click row navigates to `/chat/{report_id}`. Hover-revealed per-row delete (trash icon) with confirm-free deletion. "Clear all" button with `window.confirm` guard. Empty state with prompt to start a query. Transparent sticky nav (Lens wordmark left, Sign Out right).
- API client (`lib/api.ts`): all methods take `token: string` as first arg. `ReportData`, `ReportSource`, and `HistoryItem` types exported. `apiFetch` handles 204 No Content responses (skips `res.json()`) — required for DELETE endpoints.
- Design: dark-first (Space Grotesk font, deep navy background, violet accent). Animated gradient mesh (three drifting blobs via CSS keyframes) + SVG grain texture. Glassmorphism card with animated violet border glow on focus. Gradient wordmark. Framer Motion staggered entrance + AnimatePresence state transitions + spring-physics buttons. Typewriter placeholder.
- Database: Neon Postgres live — 5 tables (`users`, `research_jobs`, `reports`, `follow_ups`, `alembic_version`). Alembic owns all migrations; Prisma mirrors via `db pull`.
- Auth: Clerk (`@clerk/nextjs`) — `middleware.ts` and `ClerkProvider` in layout wired up, Google sign-in working, DB cleaned up (NextAuth tables dropped, `clerk_user_id` on `users`). Backend verifies Clerk JWTs and upserts users on first request (`app/auth.py`).
- LangGraph: five-node agentic graph live (`app/graph/graph.py`) — `planner_node` uses Gemini structured output (`ResearchPlan`) to decide query decomposition (1–3 Tavily sub-queries), whether to run sentiment (`run_sentiment: bool`), and a dedicated YouTube search query (`youtube_search_query`). `researcher_node` (renamed from `tavily_node`) loops over `search_queries`, merges and deduplicates results by URL, and accumulates across re-plan iterations rather than overwriting — tracks `tavily_call_count` and respects a hard cap of 5 total Tavily calls per run. `coverage_check_node` (new, step 16) uses Gemini structured output (`CoverageAssessment`) to judge whether the gathered sources are sufficient or whether to re-plan with up to 2 new sub-queries; short-circuits to `sufficient=True` without an LLM call once `iteration_count >= 3` or `tavily_call_count >= 5` (hard-cap backstop, independent of the model's opinion). `sentiment_node` fetches top YouTube comments using `youtube_search_query` and scores them with VADER. `synthesizer_node` assembles the final markdown report. Conditional edge after `coverage_check_node` (`_route_after_coverage`) loops back to `researcher` when coverage is insufficient and both caps allow it, otherwise proceeds to `sentiment_node`/`synthesizer_node` based on `run_sentiment` — same branch logic as before, just relocated past the loop. `planner_node` falls back to raw query + `run_sentiment=False` if structured output fails; `coverage_check_node` fails safe to `sufficient=True` on any error so a flaky signal can't keep the loop spinning. `call_gemini_followup()` is a standalone exported async function used by the follow-up endpoint.
- **Re-plan loop state** — `GraphState` carries `tavily_call_count`, `iteration_count`, `coverage_sufficient`, and `tried_queries` (cumulative history of every Tavily query attempted across all rounds — deliberately distinct from `search_queries`, which rotates each pass and would let the coverage-check model propose near-duplicates of earlier rounds it can no longer see). No `asyncio.sleep` between Tavily calls — Tavily is built for rapid sequential agent calls, and the hard cap (not pacing) is the cost-control lever.
- **Step 16 validated live** — tested against a narrow query with a false premise and a broad query. Confirmed: the loop re-plans on genuine gaps and exits early on genuine sufficiency (both as real LLM judgments, not just hard-cap short-circuits), both caps converge correctly as independent backstops, `tried_queries` prevents near-duplicate re-proposals, and sentiment routing branches correctly in both directions.
- `GET /reports/{report_id}` returns `query` (from job), `sources` (array from `raw_context`), and `completed_in_seconds` in addition to existing fields.
- Tavily `search_depth` is temporarily `"basic"` (1 credit/search) to conserve credits during development — switch to `"advanced"` before shipping.
- YouTube API key required (`YOUTUBE_API_KEY` in `backend/.env`) — enable YouTube Data API v3 in Google Cloud Console. Quota: 10k units/day free (search = 100 units, comment list = 1 unit/page).
- Langfuse tracing live — `@observe` decorators on `run_graph`, `planner_node`, `researcher_node`, `coverage_check_node`, `sentiment_node`, `synthesizer_node`, and `call_gemini_followup`. Credentials loaded via `Settings` (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) and pushed into `os.environ` at startup in `main.py`. Uses `langfuse==4.7.1`; import is `from langfuse import observe` (not `langfuse.decorators` or `langfuse.callback`).
- Next step: Docker + GitHub Actions + Render deploy (step 17). **pgvector / `document_chunks` semantic chunk storage and `current_step` progress-reporting both moved to v2** — researcher continues to pass merged raw sources straight to the synthesizer (and follow-ups continue to use `raw_context`); job status polling stays coarse-grained (`pending`/`running`/`done`/`failed`) for now.
- **Current graph is a true agent with a re-plan loop** — `planner → researcher → coverage_check → [conditional: loop back to researcher | sentiment? | synthesizer] → END`. Planner uses LLM decision-making via structured output; coverage_check can route back into research (max 3 iterations, 5 Tavily calls hard cap) before the conditional sentiment branch fires.

## Build order
1. Postgres schema + Alembic migrations (done)
2. FastAPI skeleton (done)
3. Clerk — auth flow end to end (done)
4. LangGraph graph — single node (Gemini only) (done)
5. Add Tavily researcher node (done)
6. Add YouTube comments + VADER sentiment node (done)
7. Implement real FastAPI report + history endpoints (stubs → real DB reads, add auth guards) (done)
8. Next.js frontend — prompt screen + progress polling (done)
9. Next.js frontend — chat screen (report card display, no follow-ups yet) (done)
10. Synthesizer prompt iteration — skipped for now, output quality acceptable
11. Next.js frontend — history screen (done)
12. Follow-up endpoint (done)
13. Next.js frontend — follow-up chat UI (done)
14. Configure Langfuse — tracing before agentic nodes (done)
15. Add Planner node — LLM decides query decomposition + whether to run sentiment (done)
16. Add Researcher node — re-plan loop (max 3 iterations, 5 Tavily calls hard cap) (done)
17. Docker + GitHub Actions + Render deploy
18. Redis caching (post-v1)
19. pgvector + `document_chunks` — semantic chunk storage and retrieval for follow-ups (post-v1)

## Key gotchas

### Next.js 16
- `params` in dynamic routes is a `Promise` — use `await params` in Server Components, `React.use(params)` in Client Components
- Turbopack is on by default for both `next dev` and `next build`
- Node.js 20+ required (`nvm use 20` before running frontend commands)

### Clerk
- Auth is handled entirely by Clerk — no NextAuth, no `@auth/prisma-adapter`
- Keys: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in `frontend/.env.local`; `CLERK_SECRET_KEY` also in `backend/.env`
- `middleware.ts` uses `clerkMiddleware` + `createRouteMatcher` to protect `/history` and `/chat`
- `<ClerkProvider>` wraps the root layout
- **Backend JWT verification**: `app/auth.py` — `get_current_user` dependency pins the token's `iss` claim against `settings.clerk_issuer` *before* fetching the JWKS (prevents an attacker-hosted JWKS from being trusted), verifies the signature (RS256) against Clerk's real keys, checks `azp` against `settings.clerk_authorized_parties`, then upserts the user into Postgres. Use as a dependency on any protected endpoint.
- **User upsert**: happens lazily on first API request — no webhook needed for local dev. A `user.created` webhook will complement this in production (deferred to step 17).
- **`getToken()` race condition**: On first page load, `getToken()` can return `null` even when `isSignedIn` is true — the session token hasn't been cached yet. Always use the `resolveToken` helper in `app/page.tsx` (tries once, waits 350ms, retries) rather than calling `getToken()` directly. Copy this pattern to any future page that makes authenticated API calls.
- **User deletion**: must be handled via a `user.deleted` Clerk webhook — no code for this yet (deferred to step 17).
- **`users` table column names**: Prisma created the table with camelCase columns (`createdAt`, etc.). The SQLAlchemy `User` model maps these explicitly (e.g. `Column("createdAt", ...)`). Do not rename them without an Alembic migration.

### Prisma v7
- Connection URL lives in `prisma.config.ts`, NOT in `prisma/schema.prisma`
- Direct DB connections require `@prisma/adapter-pg` — see `frontend/lib/prisma.ts`
- Generated client is at `lib/generated/prisma/client`, not `@prisma/client`
- After schema changes: `prisma generate` to regenerate the client

### Alembic
- Alembic owns ALL migrations — Prisma must not manage schema changes
- `users` table is excluded from autogenerate (owned by Clerk/Prisma) — see `include_object` in `backend/alembic/env.py`
- Load `.env` before running Alembic: `export $(grep -v '^#' .env | xargs) && alembic <command>`
- Neon gives a `postgresql://` URL — change to `postgresql+asyncpg://` and replace `sslmode=require` with `ssl=require` for backend use

### Backend
- Run from `backend/` with venv active: `source .venv/bin/activate`
- Start server: `uvicorn app.main:app --reload`
- Requires `backend/.env` — copy from `.env.example` and fill in values

### LangGraph
- Graph lives in `backend/app/graph/graph.py` — `run_graph` is the entry point called by `BackgroundTasks`
- Uses `gemini-3.1-flash-lite-preview` (not 1.5-flash — that model is unavailable on the current API key)
- `run_graph` opens its own `AsyncSessionLocal` session — it runs outside any request context so it cannot use the request-scoped `get_db` dependency
- `planner_node` falls back to raw query + `run_sentiment=False` on structured output failure. `researcher_node`, `coverage_check_node`, and `sentiment_node` failures are non-fatal — the graph continues with whatever sources/sentiment/coverage signal it has (coverage_check fails safe to `sufficient=True` so an unreliable signal can't keep the loop spinning). `synthesizer_node` failures set `state["error"]` which flips the job to `failed`.
- Outer `run_graph` wraps the full body in try/except to flip status to `failed` on DB errors after `status = "running"`.


### Two ORMs, one DB
- SQLAlchemy (Python) and Prisma (TypeScript) both point at the same Postgres database
- Alembic runs all migrations; Prisma uses `prisma db pull` to mirror changes

## Environment variables
- Backend: `DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY`, `CLERK_SECRET_KEY`, `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` — see `backend/.env.example`
- Frontend: `DATABASE_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` — see `frontend/.env.local.example`
