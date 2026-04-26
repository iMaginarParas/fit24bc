"""
Fit24 Fitness App - FastAPI Backend
Auth: Supabase Phone OTP via Twilio
Steps: Supabase step_logs table
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import httpx

from auth import router as auth_router
from count import router as steps_router   # ← new


# ── App lifespan ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=15.0)
    yield
    await app.state.http_client.aclose()


# ── App factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Fit24 Fitness API",
    description="Mobile OTP signup/login via Supabase + Twilio · Step sync · Leaderboard",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router,  prefix="/auth",  tags=["Auth"])
app.include_router(steps_router, prefix="/steps", tags=["Steps"])   # ← new


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "app": "Fit24", "version": "1.1.0"}