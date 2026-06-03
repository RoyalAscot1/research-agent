# Lens — Claude Working Guide

## What this is
Lens is a full-stack AI research and sentiment app. Users submit natural language queries and receive AI-generated reports combining web research with Reddit/Google Trends sentiment data. See `app_summary.md` for the full product spec.

## Repo structure
```
backend/   FastAPI + LangGraph (Python)
frontend/  Next.js 16 (TypeScript)
```

## Current state
- Backend: FastAPI skeleton with all 7 stub endpoints, async SQLAlchemy models, Alembic configured
- Frontend: Next.js 16, Tailwind, shadcn/ui, Prisma v7, stub pages for `/`, `/history`, `/chat/[id]`
- Database: Neon Postgres live — all 7 tables created (Alembic owns app tables, Prisma owns auth tables)
- Next step: NextAuth + Prisma auth flow end to end (step 3 of build order)

## Build order (from app_summary.md)
1. Postgres schema + Alembic migrations (done)
2. FastAPI skeleton (done)
3. NextAuth + Prisma — auth flow end to end ← next
4. LangGraph graph — single node (Gemini only)
5. Add Tavily researcher node
6. Add PRAW + VADER sentiment node
7. Add pytrends to sentiment node
8. Synthesizer prompt
9. Chroma integration
10. Follow-up endpoint
11. Next.js frontend — prompt screen + progress polling
12. Next.js frontend — chat screen
13. Next.js frontend — history screen
14. Docker + GitHub Actions + Render deploy
15. Redis caching (post-v1)

## Key gotchas

### Next.js 16
- `params` in dynamic routes is a `Promise` — always `await params` before destructuring
- Turbopack is on by default for both `next dev` and `next build`
- Node.js 20+ required (`nvm use 20` before running frontend commands)

### Prisma v7
- Connection URL lives in `prisma.config.ts`, NOT in `prisma/schema.prisma`
- Direct DB connections require `@prisma/adapter-pg` — see `frontend/lib/prisma.ts`
- Generated client is at `lib/generated/prisma/client`, not `@prisma/client`
- After schema changes: `prisma generate` to regenerate the client

### Alembic
- Alembic owns ALL migrations — Prisma must not manage schema changes
- `users` table is excluded from autogenerate (owned by Prisma/NextAuth) — see `include_object` in `backend/alembic/env.py`
- Load `.env` before running Alembic: `export $(grep -v '^#' .env | xargs) && alembic <command>`
- Neon gives a `postgresql://` URL — change to `postgresql+asyncpg://` and replace `sslmode=require` with `ssl=require` for backend use

### Backend
- Run from `backend/` with venv active: `source .venv/bin/activate`
- Start server: `uvicorn app.main:app --reload`
- Requires `backend/.env` — copy from `.env.example` and fill in values

### Two ORMs, one DB
- SQLAlchemy (Python) and Prisma (TypeScript) both point at the same Postgres database
- Alembic runs all migrations; Prisma uses `prisma db pull` to mirror changes

## Environment variables
- Backend: `DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` — see `backend/.env.example`
- Frontend: `DATABASE_URL`, `NEXT_PUBLIC_API_URL`, `AUTH_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — see `frontend/.env.local.example`
