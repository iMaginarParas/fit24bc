"""
Fit24 Fitness App - FastAPI Backend
Auth: Supabase Phone OTP via Twilio
Steps: Supabase step_logs table
Profile: Supabase user_profiles table (onboarding + edit)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import httpx

from auth import router as auth_router
from count import router as steps_router
from onboarding import router as profile_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=15.0)
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="Fit24 Fitness API",
    description="OTP Auth · Step Sync · Leaderboard · User Profiles",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router,    prefix="/auth",    tags=["Auth"])
app.include_router(steps_router,   prefix="/steps",   tags=["Steps"])
app.include_router(profile_router, prefix="/profile", tags=["Profile"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "app": "Fit24", "version": "1.2.0"}