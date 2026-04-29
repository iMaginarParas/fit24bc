"""
count.py  –  Fit24 step-count storage & retrieval
--------------------------------------------------
Endpoints
  POST /steps/sync          →  upsert today's step count for the authed user
  GET  /steps/today         →  get today's count for the authed user
  GET  /steps/history       →  get last N days of step history
  GET  /steps/leaderboard   →  top users by total steps (weekly)

Auth: every request requires  Authorization: Bearer <supabase_access_token>
The JWT is verified by Supabase; we extract the user's UUID from it.

Supabase table  (run this SQL in your Supabase SQL editor):
─────────────────────────────────────────────────────────────
create table if not exists public.step_logs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  log_date    date not null,
  steps       integer not null default 0,
  calories    integer generated always as (steps / 20) stored,
  distance_m  integer generated always as (steps * 75 / 100) stored,
  synced_at   timestamptz not null default now(),
  unique (user_id, log_date)
);

-- Row-level security: users can only read/write their own rows
alter table public.step_logs enable row level security;

create policy "users manage own logs"
  on public.step_logs for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Index for leaderboard query
-- Index for leaderboard query
create index if not exists step_logs_date_steps
  on public.step_logs (log_date desc, steps desc);

create table if not exists public.activity_sessions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  type        int not null, -- 0: walk, 1: run, 2: cycle
  distance    numeric(10,2) not null default 0,
  duration    integer not null default 0,
  steps       integer not null default 0,
  calories    integer not null default 0,
  route       jsonb default '[]',
  created_at  timestamptz not null default now()
);

alter table public.activity_sessions enable row level security;

create policy "users manage own sessions"
  on public.activity_sessions for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional, List, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter()

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _anon_headers() -> dict:
    return {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}


def _user_headers(token: str) -> dict:
    """Headers that let Supabase apply RLS using the user's JWT."""
    return {
        **_anon_headers(),
        "Authorization": f"Bearer {token}",
    }


def _service_headers() -> dict:
    """Service-role headers — bypasses RLS (leaderboard only)."""
    key = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


# ── Auth dependency ───────────────────────────────────────────────────────────

async def _get_user(request: Request) -> dict:
    """
    Validate the Bearer JWT with Supabase and return the user dict.
    Raises 401 if missing / invalid.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing or invalid Authorization header")
    token = auth_header.split(" ", 1)[1]
    client: httpx.AsyncClient = request.app.state.http_client

    resp = await client.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers=_user_headers(token),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired token")
    data = resp.json()
    return {"id": data["id"], "phone": data.get("phone", ""), "token": token}


# ── Schemas ───────────────────────────────────────────────────────────────────

class StepSyncRequest(BaseModel):
    steps: int = Field(..., ge=0, le=200_000,
                       description="Total step count for the given date")
    log_date: Optional[date] = Field(
        None,
        description="Date of the steps (ISO 8601). Defaults to today (UTC).",
    )

    @field_validator("log_date", mode="before")
    @classmethod
    def default_today(cls, v):
        return v or date.today().isoformat()


class StepSyncResponse(BaseModel):
    message: str
    log_date: str
    steps: int
    calories: int
    distance_m: int
    fit_points: int


class DayLog(BaseModel):
    log_date: str
    steps: int
    calories: int
    distance_m: int
    fit_points: int


class HistoryResponse(BaseModel):
    days: list[DayLog]
    total_steps: int
    total_fit_points: int


class LeaderEntry(BaseModel):
    rank: int
    user_id: str
    steps: int
    fit_points: int


class LeaderboardResponse(BaseModel):
    week_start: str
    entries: list[LeaderEntry]


class ActivitySessionRequest(BaseModel):
    type: int
    distance: float
    duration: int
    steps: int
    calories: int
    route: List[dict]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_points(steps: int) -> int:
    """1 pt per step — matches Flutter gamification formula."""
    return steps


def _sb_error(resp: httpx.Response) -> HTTPException:
    try:
        detail = resp.json().get("message") or resp.text
    except Exception:
        detail = resp.text
    return HTTPException(status_code=resp.status_code, detail=detail)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/sync",
    response_model=StepSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert step count for a given date",
)
async def sync_steps(
    body: StepSyncRequest,
    request: Request,
    user: dict = Depends(_get_user),
):
    """
    Called by the Flutter app whenever step data changes (e.g. every minute or
    on app foreground). Uses Supabase's UPSERT so repeated calls are safe.

    Returns the stored record enriched with calories, distance, and fit points.
    """
    client: httpx.AsyncClient = request.app.state.http_client
    log_date = str(body.log_date or date.today())

    payload = {
        "user_id":  user["id"],
        "log_date": log_date,
        "steps":    body.steps,
        # synced_at has a DB default; omit it to avoid storing literal "now()"
    }

    # UPSERT on (user_id, log_date) — always keep latest step value
    url = (
        f"{SUPABASE_URL}/rest/v1/step_logs"
        f"?on_conflict=user_id,log_date"
    )
    resp = await client.post(
        url,
        headers={
            **_user_headers(user["token"]),
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
        json=payload,
    )

    if resp.status_code not in (200, 201):
        raise _sb_error(resp)

    rows = resp.json()
    row  = rows[0] if isinstance(rows, list) else rows

    # Supabase computed columns may or may not be returned; compute locally too
    steps      = row.get("steps", body.steps)
    calories   = row.get("calories", steps // 20)
    distance_m = row.get("distance_m", steps * 75 // 100)

    return StepSyncResponse(
        message    ="Steps synced successfully.",
        log_date   = log_date,
        steps      = steps,
        calories   = calories,
        distance_m = distance_m,
        fit_points = _to_points(steps),
    )


@router.get(
    "/today",
    response_model=DayLog,
    status_code=status.HTTP_200_OK,
    summary="Get today's step log for the authenticated user",
)
async def get_today(
    request: Request,
    user: dict = Depends(_get_user),
):
    client: httpx.AsyncClient = request.app.state.http_client
    today = date.today().isoformat()

    url = (
        f"{SUPABASE_URL}/rest/v1/step_logs"
        f"?user_id=eq.{user['id']}&log_date=eq.{today}&limit=1"
    )
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)

    rows = resp.json()
    if not rows:
        # No entry yet — return zeros
        return DayLog(
            log_date   = today,
            steps      = 0,
            calories   = 0,
            distance_m = 0,
            fit_points = 0,
        )

    row = rows[0]
    steps = row.get("steps", 0)
    return DayLog(
        log_date   = row["log_date"],
        steps      = steps,
        calories   = row.get("calories", steps // 20),
        distance_m = row.get("distance_m", steps * 75 // 100),
        fit_points = _to_points(steps),
    )


@router.get(
    "/stats",
    status_code=status.HTTP_200_OK,
    summary="Get lifetime total steps and points",
)
async def get_stats(
    request: Request,
    user: dict = Depends(_get_user),
):
    """Returns lifetime totals for steps and fit points."""
    client: httpx.AsyncClient = request.app.state.http_client

    # Query all step logs for this user
    # Note: For massive scale, you'd want a cached 'stats' table, 
    # but for now, summing step_logs is fine.
    url = (
        f"{SUPABASE_URL}/rest/v1/step_logs"
        f"?user_id=eq.{user['id']}"
        f"&select=steps"
    )
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)

    rows = resp.json()
    total_steps = sum(r.get("steps", 0) for r in rows)
    
    # Also include session steps
    url_sessions = (
        f"{SUPABASE_URL}/rest/v1/activity_sessions"
        f"?user_id=eq.{user['id']}"
        f"&select=steps"
    )
    resp_s = await client.get(url_sessions, headers=_user_headers(user["token"]))
    session_steps = 0
    if resp_s.status_code == 200:
        session_steps = sum(r.get("steps", 0) for r in resp_s.json())

    combined_total = total_steps + session_steps

    return {
        "total_steps": combined_total,
        "total_fit_points": _to_points(combined_total),
        "total_sessions": len(resp_s.json()) if resp_s.status_code == 200 else 0
    }


@router.get(
    "/history",
    response_model=HistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get last N days of step history",
)
async def get_history(
    request: Request,
    days: int = 7,
    user: dict = Depends(_get_user),
):
    """Returns up to `days` days of history (default 7, max 90)."""
    days = min(max(days, 1), 90)
    client: httpx.AsyncClient = request.app.state.http_client

    since = (date.today() - timedelta(days=days - 1)).isoformat()
    url = (
        f"{SUPABASE_URL}/rest/v1/step_logs"
        f"?user_id=eq.{user['id']}&log_date=gte.{since}"
        f"&order=log_date.desc&limit={days}"
    )
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)

    rows  = resp.json()
    total = sum(r.get("steps", 0) for r in rows)

    day_logs = [
        DayLog(
            log_date   = r["log_date"],
            steps      = r.get("steps", 0),
            calories   = r.get("calories", r.get("steps", 0) // 20),
            distance_m = r.get("distance_m", r.get("steps", 0) * 75 // 100),
            fit_points = _to_points(r.get("steps", 0)),
        )
        for r in rows
    ]

    return HistoryResponse(
        days              = day_logs,
        total_steps       = total,
        total_fit_points  = _to_points(total),
    )


@router.get(
    "/leaderboard",
    response_model=LeaderboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Weekly leaderboard (top 20 users by steps)",
)
async def get_leaderboard(request: Request):
    """
    Public leaderboard – no auth required. Aggregates the current calendar
    week (Mon–Sun). Uses service role so RLS is bypassed.
    """
    client: httpx.AsyncClient = request.app.state.http_client
    today     = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end   = week_start + timedelta(days=6)

    # Supabase REST doesn't support GROUP BY natively, so we fetch rows and
    # aggregate in Python. For large datasets, replace with a Supabase RPC.
    url = (
        f"{SUPABASE_URL}/rest/v1/step_logs"
        f"?log_date=gte.{week_start}&log_date=lte.{week_end}"
        f"&order=steps.desc&limit=500"
    )
    resp = await client.get(url, headers=_service_headers())
    if resp.status_code != 200:
        raise _sb_error(resp)

    rows = resp.json()

    # Aggregate per user
    totals: dict[str, int] = {}
    for r in rows:
        uid = r["user_id"]
        totals[uid] = totals.get(uid, 0) + r.get("steps", 0)

    sorted_users = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:20]

    entries = [
        LeaderEntry(
            rank       = i + 1,
            user_id    = uid,
            steps      = steps,
            fit_points = _to_points(steps),
        )
        for i, (uid, steps) in enumerate(sorted_users)
    ]

    return LeaderboardResponse(
        week_start = str(week_start),
        entries    = entries,
    )


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def save_session(
    body: ActivitySessionRequest,
    request: Request,
    user: dict = Depends(_get_user),
):
    """Save a GPS activity session."""
    client: httpx.AsyncClient = request.app.state.http_client
    payload = {
        "user_id": user["id"],
        "type": body.type,
        "distance": body.distance,
        "duration": body.duration,
        "steps": body.steps,
        "calories": body.calories,
        "route": body.route,
    }
    url = f"{SUPABASE_URL}/rest/v1/activity_sessions"
    resp = await client.post(url, headers=_user_headers(user["token"]), json=payload)
    if resp.status_code not in (200, 201):
        raise _sb_error(resp)
    return {"status": "saved"}


@router.get("/sessions")
async def get_sessions(
    request: Request,
    user: dict = Depends(_get_user),
):
    """Fetch user's recent sessions."""
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/activity_sessions?user_id=eq.{user['id']}&order=created_at.desc&limit=20"
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)
    return resp.json()