from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
import os
from supabase import create_client, Client

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Step Count API",
    description="Backend for syncing step counts from Android (Google Fit / Health Connect) and iOS (HealthKit)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]   # service-role key (server-side only)
JWT_SECRET = os.environ.get("JWT_SECRET", "")               # optional extra signing secret

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ── Security ─────────────────────────────────────────────────────────────────
bearer_scheme = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """Verify the Supabase JWT sent by the mobile client."""
    try:
        user = supabase.auth.get_user(credentials.credentials)
        if user is None or user.user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return {"id": user.user.id, "email": user.user.email}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

# ── Schemas ───────────────────────────────────────────────────────────────────
class Platform(str):
    ANDROID = "android"
    IOS = "ios"

class StepEntry(BaseModel):
    """Single day step record sent from the device."""
    date: date                          # e.g. "2024-04-10"
    steps: int = Field(..., ge=0)
    platform: str = Field(..., pattern="^(android|ios)$")
    source: Optional[str] = None        # "health_connect" | "google_fit" | "healthkit"
    distance_meters: Optional[float] = None
    calories: Optional[float] = None
    active_minutes: Optional[int] = None

class StepBulkSync(BaseModel):
    """Batch upload – mobile clients send up to 90 days at once."""
    entries: List[StepEntry] = Field(..., max_length=365)

class StepResponse(BaseModel):
    id: str
    user_id: str
    date: date
    steps: int
    platform: str
    source: Optional[str]
    distance_meters: Optional[float]
    calories: Optional[float]
    active_minutes: Optional[int]
    synced_at: datetime

class UserProfile(BaseModel):
    daily_goal: Optional[int] = 10_000
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "service": "step-count-api"}

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}

# ── Steps endpoints ───────────────────────────────────────────────────────────

@app.post("/steps", status_code=status.HTTP_201_CREATED, tags=["steps"])
def upsert_steps(
    entry: StepEntry,
    user: dict = Depends(get_current_user),
):
    """
    Insert or update a single day's step count.
    Uses upsert so the mobile app can call this freely on re-sync.
    """
    row = {
        "user_id": user["id"],
        "date": entry.date.isoformat(),
        "steps": entry.steps,
        "platform": entry.platform,
        "source": entry.source,
        "distance_meters": entry.distance_meters,
        "calories": entry.calories,
        "active_minutes": entry.active_minutes,
    }
    result = (
        supabase.table("step_counts")
        .upsert(row, on_conflict="user_id,date")
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save step data")
    return result.data[0]


@app.post("/steps/sync", status_code=status.HTTP_200_OK, tags=["steps"])
def bulk_sync_steps(
    payload: StepBulkSync,
    user: dict = Depends(get_current_user),
):
    """
    Bulk upsert – ideal for the first launch or after a long offline period.
    Android / iOS apps typically sync 7-90 days of history here.
    """
    rows = [
        {
            "user_id": user["id"],
            "date": e.date.isoformat(),
            "steps": e.steps,
            "platform": e.platform,
            "source": e.source,
            "distance_meters": e.distance_meters,
            "calories": e.calories,
            "active_minutes": e.active_minutes,
        }
        for e in payload.entries
    ]
    result = (
        supabase.table("step_counts")
        .upsert(rows, on_conflict="user_id,date")
        .execute()
    )
    return {"synced": len(result.data), "records": result.data}


@app.get("/steps", response_model=List[StepResponse], tags=["steps"])
def get_steps(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user: dict = Depends(get_current_user),
):
    """
    Fetch the authenticated user's step history.
    Optionally filter by date range (YYYY-MM-DD).
    """
    query = (
        supabase.table("step_counts")
        .select("*")
        .eq("user_id", user["id"])
        .order("date", desc=True)
    )
    if start_date:
        query = query.gte("date", start_date.isoformat())
    if end_date:
        query = query.lte("date", end_date.isoformat())

    result = query.execute()
    return result.data


@app.get("/steps/today", tags=["steps"])
def get_today_steps(user: dict = Depends(get_current_user)):
    """Quick endpoint – returns today's step count for the home screen widget."""
    today = date.today().isoformat()
    result = (
        supabase.table("step_counts")
        .select("*")
        .eq("user_id", user["id"])
        .eq("date", today)
        .maybe_single()
        .execute()
    )
    if not result.data:
        return {"date": today, "steps": 0, "message": "No data for today yet"}
    return result.data


@app.get("/steps/summary", tags=["steps"])
def get_summary(user: dict = Depends(get_current_user)):
    """
    Returns aggregate stats: total steps, average daily steps, best day,
    and current streak – all computed server-side.
    """
    result = (
        supabase.table("step_counts")
        .select("date, steps")
        .eq("user_id", user["id"])
        .order("date", desc=False)
        .execute()
    )
    records = result.data
    if not records:
        return {"total_steps": 0, "avg_daily_steps": 0, "best_day": None, "streak_days": 0}

    steps_list = [r["steps"] for r in records]
    total = sum(steps_list)
    avg = total // len(steps_list)
    best = max(records, key=lambda r: r["steps"])

    # Simple consecutive-day streak (most recent)
    streak = 0
    dates = sorted([date.fromisoformat(r["date"]) for r in records], reverse=True)
    today = date.today()
    for i, d in enumerate(dates):
        expected = today - __import__("datetime").timedelta(days=i)
        if d == expected:
            streak += 1
        else:
            break

    return {
        "total_steps": total,
        "avg_daily_steps": avg,
        "best_day": {"date": best["date"], "steps": best["steps"]},
        "streak_days": streak,
        "total_days_tracked": len(records),
    }


# ── User Profile ──────────────────────────────────────────────────────────────

@app.get("/profile", tags=["profile"])
def get_profile(user: dict = Depends(get_current_user)):
    result = (
        supabase.table("user_profiles")
        .select("*")
        .eq("user_id", user["id"])
        .maybe_single()
        .execute()
    )
    return result.data or {"user_id": user["id"], "daily_goal": 10_000}


@app.put("/profile", tags=["profile"])
def update_profile(profile: UserProfile, user: dict = Depends(get_current_user)):
    row = {"user_id": user["id"], **profile.model_dump(exclude_none=True)}
    result = (
        supabase.table("user_profiles")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    return result.data[0]