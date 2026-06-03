from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import history, jobs, queries, reports

app = FastAPI(title="Lens API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(queries.router)
app.include_router(jobs.router)
app.include_router(reports.router)
app.include_router(history.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
