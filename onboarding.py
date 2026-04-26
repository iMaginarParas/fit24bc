"""
onboarding.py  –  Fit24 user profile setup & retrieval
-------------------------------------------------------
Endpoints
  POST /profile/setup     →  create / upsert profile after onboarding
  GET  /profile/me        →  fetch current user's profile
  PATCH /profile/me       →  edit profile fields (from profile settings page)

Supabase table  (run in SQL editor):
─────────────────────────────────────────────────────────
create table if not exists public.user_profiles (
  id             uuid primary key references auth.users(id) on delete cascade,
  phone          text,
  gender         text,
  age            int,
  weight_kg      numeric(5,1),
  height_cm      int,
  daily_goal     int  default 8000,
  focus_areas    text[]  default '{}',
  exercise_freq  text,
  exercise_types text[]  default '{}',
  city           text,
  created_at     timestamptz default now(),
  updated_at     timestamptz default now()
);

alter table public.user_profiles enable row level security;

create policy "users manage own profile"
  on public.user_profiles for all
  using  (auth.uid() = id)
  with check (auth.uid() = id);
─────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

router = APIRouter()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY: str = os.environ["SUPABASE_ANON_KEY"]


def _anon_headers() -> dict:
    return {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}


def _user_headers(token: str) -> dict:
    return {**_anon_headers(), "Authorization": f"Bearer {token}"}


# ── Auth dependency (same pattern as count.py) ───────────────────────────────

async def _get_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth.split(" ", 1)[1]
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers=_user_headers(token),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    d = resp.json()
    return {"id": d["id"], "phone": d.get("phone", ""), "token": token}


def _sb_error(resp: httpx.Response) -> HTTPException:
    try:
        detail = resp.json().get("message") or resp.text
    except Exception:
        detail = resp.text
    return HTTPException(status_code=resp.status_code, detail=detail)


# ── Schemas ──────────────────────────────────────────────────────────────────

class ProfileSetupRequest(BaseModel):
    gender:         Optional[str]       = None
    age:            Optional[int]       = Field(None, ge=5,  le=120)
    weight_kg:      Optional[float]     = Field(None, ge=20, le=300)
    height_cm:      Optional[int]       = Field(None, ge=50, le=300)
    daily_goal:     Optional[int]       = Field(None, ge=1000, le=100_000)
    focus_areas:    Optional[List[str]] = None
    exercise_freq:  Optional[str]       = None
    exercise_types: Optional[List[str]] = None
    city:           Optional[str]       = None


class ProfileResponse(BaseModel):
    id:             str
    phone:          Optional[str]       = None
    gender:         Optional[str]       = None
    age:            Optional[int]       = None
    weight_kg:      Optional[float]     = None
    height_cm:      Optional[int]       = None
    daily_goal:     int                 = 8000
    focus_areas:    List[str]           = []
    exercise_freq:  Optional[str]       = None
    exercise_types: List[str]           = []
    city:           Optional[str]       = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/setup",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or update user profile (called after onboarding)",
)
async def setup_profile(
    body: ProfileSetupRequest,
    request: Request,
    user: dict = Depends(_get_user),
):
    """
    Upserts the user profile. Safe to call multiple times —
    subsequent calls update existing fields only.
    """
    client: httpx.AsyncClient = request.app.state.http_client

    payload = {"id": user["id"], "phone": user["phone"]}
    # Only include fields the user actually filled in
    for field in body.model_fields:
        val = getattr(body, field)
        if val is not None:
            payload[field] = val
    payload["updated_at"] = "now()"

    url = f"{SUPABASE_URL}/rest/v1/user_profiles?on_conflict=id"
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
    row  = rows[0] if isinstance(rows, list) and rows else payload
    return _row_to_profile(row)


@router.get(
    "/me",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current user's profile",
)
async def get_profile(
    request: Request,
    user: dict = Depends(_get_user),
):
    client: httpx.AsyncClient = request.app.state.http_client
    url = (f"{SUPABASE_URL}/rest/v1/user_profiles"
           f"?id=eq.{user['id']}&limit=1")
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)
    rows = resp.json()
    if not rows:
        # Profile not created yet — return empty shell
        return ProfileResponse(id=user["id"], phone=user["phone"])
    return _row_to_profile(rows[0])


@router.patch(
    "/me",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Edit profile fields (from profile settings)",
)
async def edit_profile(
    body: ProfileSetupRequest,
    request: Request,
    user: dict = Depends(_get_user),
):
    """Partial update — only sends non-null fields to Supabase."""
    client: httpx.AsyncClient = request.app.state.http_client

    payload: dict = {"updated_at": "now()"}
    for field in body.model_fields:
        val = getattr(body, field)
        if val is not None:
            payload[field] = val

    if len(payload) == 1:   # only updated_at — nothing to do
        return await get_profile(request, user)

    url = (f"{SUPABASE_URL}/rest/v1/user_profiles"
           f"?id=eq.{user['id']}")
    resp = await client.patch(
        url,
        headers={
            **_user_headers(user["token"]),
            "Prefer": "return=representation",
        },
        json=payload,
    )
    if resp.status_code not in (200, 204):
        raise _sb_error(resp)

    rows = resp.json()
    row  = rows[0] if isinstance(rows, list) and rows else payload
    return _row_to_profile({**{"id": user["id"], "phone": user["phone"]}, **row})


# ── Helper ───────────────────────────────────────────────────────────────────

def _row_to_profile(row: dict) -> ProfileResponse:
    return ProfileResponse(
        id            = row.get("id",             ""),
        phone         = row.get("phone"),
        gender        = row.get("gender"),
        age           = row.get("age"),
        weight_kg     = row.get("weight_kg"),
        height_cm     = row.get("height_cm"),
        daily_goal    = row.get("daily_goal",     8000),
        focus_areas   = row.get("focus_areas",    []) or [],
        exercise_freq = row.get("exercise_freq"),
        exercise_types= row.get("exercise_types", []) or [],
        city          = row.get("city"),
    )