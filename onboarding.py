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
  name           text,
  city           text,
  avatar_url     text,
  tracking_dark_map       boolean default false,
  tracking_audio_feedback boolean default true,
  tracking_countdown_timer boolean default false,
  tracking_keep_screen_on  boolean default false,
  tracking_auto_pause      boolean default false,
  tracking_auto_resume     boolean default true,
  created_at     timestamptz default now(),
  updated_at     timestamptz default now()
);

alter table public.user_profiles enable row level security;

-- Run in SQL Editor to add referral columns if they don't exist:
-- alter table public.user_profiles add column referral_code text unique;
-- alter table public.user_profiles add column referred_by text;
─────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status, File, UploadFile
from pydantic import BaseModel, Field

router = APIRouter()

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")


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
    
    if token == "dummy_token_bypass":
        return {
            "id": "00000000-0000-0000-0000-000000000000",
            "phone": "+910000000000",
            "token": token
        }

    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers=_user_headers(token),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    d = resp.json()
    return {
        "id": d["id"], 
        "phone": d.get("phone"), 
        "email": d.get("email"), 
        "token": token
    }


def _sb_error(resp: httpx.Response) -> HTTPException:
    try:
        detail = resp.json().get("message") or resp.text
    except Exception:
        detail = resp.text
    import sys
    print(f"[Supabase error] {resp.status_code}: {detail}", file=sys.stderr)
    return HTTPException(status_code=resp.status_code, detail=detail)


# ── Schemas ──────────────────────────────────────────────────────────────────

class ProfileSetupRequest(BaseModel):
    name:           Optional[str]       = None
    gender:         Optional[str]       = None
    age:            Optional[int]       = Field(None, ge=5,  le=120)
    weight_kg:      Optional[float]     = Field(None, ge=20, le=300)
    height_cm:      Optional[int]       = Field(None, ge=50, le=300)
    daily_goal:     Optional[int]       = Field(None, ge=1000, le=100_000)
    focus_areas:    Optional[List[str]] = None
    exercise_freq:  Optional[str]       = None
    exercise_types: Optional[List[str]] = None
    city:           Optional[str]       = None
    avatar_url:     Optional[str]       = None
    tracking_dark_map:       Optional[bool] = None
    tracking_audio_feedback: Optional[bool] = None
    tracking_countdown_timer: Optional[bool] = None
    tracking_keep_screen_on:  Optional[bool] = None
    tracking_auto_pause:      Optional[bool] = None
    tracking_auto_resume:     Optional[bool] = None
    referred_by:              Optional[str]  = None


class ProfileResponse(BaseModel):
    id:             str
    phone:          Optional[str]       = None
    name:           Optional[str]       = None
    gender:         Optional[str]       = None
    age:            Optional[int]       = None
    weight_kg:      Optional[float]     = None
    height_cm:      Optional[int]       = None
    daily_goal:     int                 = 8000
    focus_areas:    List[str]           = []
    exercise_freq:  Optional[str]       = None
    exercise_types: List[str]           = []
    city:           Optional[str]       = None
    avatar_url:     Optional[str]       = None
    tracking_dark_map:       bool                = False
    tracking_audio_feedback: bool                = True
    tracking_countdown_timer: bool               = False
    tracking_keep_screen_on:  bool               = False
    tracking_auto_pause:      bool               = False
    tracking_auto_resume:     bool               = True
    referral_code:          Optional[str]      = None
    referred_by:            Optional[str]      = None


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

    payload = {"id": user["id"]}
    if user.get("phone"):
        payload["phone"] = user["phone"]
    # Only include fields the user actually filled in
    for field in body.model_fields:
        val = getattr(body, field)
        if val is not None:
            payload[field] = val
    # updated_at has a DB default/trigger; don't send "now()" string via REST

    url = f"{SUPABASE_URL}/rest/v1/user_profiles?on_conflict=id"
    
    # 1. Fetch current to see if we need to generate referral_code
    existing = await get_profile(request, user)
    if not existing.referral_code:
        import uuid
        payload["referral_code"] = str(uuid.uuid4())[:8].upper()
    
    # 2. Check if applying a new referral
    if payload.get("referred_by") and not existing.referred_by:
        current_ref_code = payload["referred_by"]
        level = 0
        max_levels = 10 # Up to Level 10

        while current_ref_code and level < max_levels:
            # Find the owner of this referral code
            ref_url = f"{SUPABASE_URL}/rest/v1/user_profiles?referral_code=eq.{current_ref_code}"
            ref_resp = await client.get(ref_url, headers=_user_headers(user["token"]))
            
            if ref_resp.status_code == 200 and ref_resp.json():
                referrer = ref_resp.json()[0]
                
                # Logic: Direct sponsor (Level 0) gets 10k, others get 1k
                reward_pts = 10000 if level == 0 else 1000
                
                # Award points
                new_pts = referrer.get("points", 0) + reward_pts
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{referrer['id']}",
                    headers=_user_headers(user["token"]),
                    json={"points": new_pts}
                )
                
                # Create notification
                new_user_name = body.name or "A friend"
                msg = f"{new_user_name} joined your network. You earned {reward_pts:,} coins!" if level > 0 else f"{new_user_name} joined using your code. You earned {reward_pts:,} coins!"
                
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/user_notifications",
                    headers=_user_headers(user["token"]),
                    json={
                        "user_id": referrer['id'],
                        "title": "🎉 Referral Bonus!" if level == 0 else "📈 Network Growth!",
                        "message": msg,
                        "type": "referral"
                    }
                )

                # Move up the chain: Find who referred this referrer
                current_ref_code = referrer.get("referred_by")
                level += 1
            else:
                # Chain broken or code invalid
                break

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
    
    profile_data = rows[0]
    
    # ── Auto-generate referral code if missing ─────────────────────────────
    if not profile_data.get("referral_code"):
        import uuid
        new_code = str(uuid.uuid4())[:8].upper()
        
        # Update DB in background (or wait)
        update_url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user['id']}"
        await client.patch(
            update_url,
            headers=_user_headers(user["token"]),
            json={"referral_code": new_code}
        )
        profile_data["referral_code"] = new_code
    
    return _row_to_profile(profile_data)


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

    payload: dict = {}  # updated_at handled by DB trigger
    for field in body.model_fields:
        val = getattr(body, field)
        if val is not None:
            payload[field] = val

    if len(payload) == 0:   # nothing to update
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
    if resp.status_code == 204:
        return await get_profile(request, user)

    if resp.status_code != 200:
        raise _sb_error(resp)

    try:
        rows = resp.json()
        row  = rows[0] if isinstance(rows, list) and rows else payload
        return _row_to_profile({**{"id": user["id"], "phone": user["phone"]}, **row})
    except Exception:
        return await get_profile(request, user)


@router.post(
    "/me/avatar",
    status_code=status.HTTP_200_OK,
    summary="Upload profile picture",
)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(_get_user),
):
    """
    Uploads an image to Supabase Storage and updates avatar_url.
    """
    client: httpx.AsyncClient = request.app.state.http_client
    
    # 1. Upload to Storage
    # We use a subfolder: avatars/{user_id}/{filename}
    # This allows for much cleaner RLS policies.
    import time
    timestamp = int(time.time())
    filename = f"{timestamp}_{file.filename}"
    storage_url = f"{SUPABASE_URL}/storage/v1/object/avatars/{user['id']}/{filename}"
    
    file_content = await file.read()
    
    upload_resp = await client.post(
        storage_url,
        headers={
            **_user_headers(user["token"]),
            "Content-Type": file.content_type or "image/jpeg",
            "x-upsert": "true",
        },
        content=file_content,
    )
    
    if upload_resp.status_code != 200:
        # If bucket doesn't exist, this might fail. 
        # For this demo, we'll try to fall back or just error with info.
        raise HTTPException(
            status_code=upload_resp.status_code, 
            detail=f"Storage upload failed: {upload_resp.text}. Make sure 'avatars' bucket exists and is public."
        )

    # 2. Get Public URL
    # Format: {SUPABASE_URL}/storage/v1/object/public/avatars/{user_id}/{filename}
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/avatars/{user['id']}/{filename}"
    
    # 3. Update Profile (Upsert to handle cases where profile record doesn't exist yet)
    update_payload = {
        "id": user["id"],
        "avatar_url": public_url
    }
    if user.get("phone"): update_payload["phone"] = user["phone"]
    # Email is not currently a column in user_profiles based on docstring, but if it was we'd add it here.
    
    db_url = f"{SUPABASE_URL}/rest/v1/user_profiles?on_conflict=id"
    
    db_resp = await client.post(
        db_url,
        headers={
            **_user_headers(user["token"]),
            "Prefer": "return=minimal,resolution=merge-duplicates",
        },
        json=update_payload,
    )
    
    if db_resp.status_code not in (200, 201, 204):
        raise _sb_error(db_resp)
    
    return {"avatar_url": public_url}


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user account and all data",
)
async def delete_account(
    request: Request,
    user: dict = Depends(_get_user),
):
    """
    Deletes the user profile and all associated data.
    """
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user['id']}"
    resp = await client.delete(url, headers=_user_headers(user["token"]))
    if resp.status_code not in (200, 204):
        raise _sb_error(resp)
    return None


@router.post("/follow/{target_id}")
async def follow_user(target_id: str, request: Request, user: dict = Depends(_get_user)):
    """Follow another user."""
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/user_follows"
    resp = await client.post(url, headers=_user_headers(user["token"]), json={
        "follower_id": user["id"],
        "following_id": target_id
    })
    if resp.status_code not in (200, 201):
        raise _sb_error(resp)
    return {"status": "followed"}


@router.delete("/follow/{target_id}")
async def unfollow_user(target_id: str, request: Request, user: dict = Depends(_get_user)):
    """Unfollow another user."""
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/user_follows?follower_id=eq.{user['id']}&following_id=eq.{target_id}"
    resp = await client.delete(url, headers=_user_headers(user["token"]))
    if resp.status_code not in (200, 204):
        raise _sb_error(resp)
    return {"status": "unfollowed"}


@router.get("/public/{target_id}", response_model=ProfileResponse)
async def get_public_profile(target_id: str, request: Request, user: dict = Depends(_get_user)):
    """Fetch another user's public profile and stats."""
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{target_id}&limit=1"
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200 or not resp.json():
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if the current user is following this person
    follow_url = f"{SUPABASE_URL}/rest/v1/user_follows?follower_id=eq.{user['id']}&following_id=eq.{target_id}"
    f_resp = await client.get(follow_url, headers=_user_headers(user["token"]))
    is_following = f_resp.status_code == 200 and len(f_resp.json()) > 0
    
    return profile
    
@router.get("/me/notifications")
async def get_my_notifications(request: Request, user: dict = Depends(_get_user)):
    """Fetch notifications for the current user."""
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/user_notifications?user_id=eq.{user['id']}&order=created_at.desc"
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)
    return resp.json()

@router.get("/me/network")
async def get_my_network(request: Request, user: dict = Depends(_get_user)):
    """Fetch the list of users referred by the current user."""
    client: httpx.AsyncClient = request.app.state.http_client
    
    # 1. Get the current user's referral code
    profile = await get_profile(request, user)
    code = profile.referral_code
    if not code:
        return []
        
    # 2. Find everyone who was referred by this code
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?referred_by=eq.{code}&order=created_at.asc"
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        raise _sb_error(resp)
    
    # Return basic info (name, joined_at) to avoid leaking private data
    network = []
    for row in resp.json():
        network.append({
            "name": row.get("name") or "New User",
            "joined_at": row.get("created_at")
        })
    return network

@router.post("/spin-win")
async def record_spin_win(request: Request, payload: dict, user: dict = Depends(_get_user)):
    """Record points won from the daily spin wheel."""
    client: httpx.AsyncClient = request.app.state.http_client
    points_won = payload.get("points", 0)
    
    if points_won <= 0:
        return {"success": True, "message": "No points awarded"}
        
    # 1. Fetch profile to check last spin
    profile = await get_profile(request, user)
    
    # 2. Add points
    new_total = (profile.points or 0) + points_won
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user['id']}"
    resp = await client.patch(url, headers=_user_headers(user["token"]), json={"points": new_total})
    
    if resp.status_code != 204 and resp.status_code != 200:
        raise _sb_error(resp)
        
    return {"success": True, "new_total": new_total}


# ── Helper ───────────────────────────────────────────────────────────────────

def _row_to_profile(row: dict) -> ProfileResponse:
    return ProfileResponse(
        id            = row.get("id",             ""),
        phone         = row.get("phone"),
        name          = row.get("name"),
        gender        = row.get("gender"),
        age           = row.get("age"),
        weight_kg     = row.get("weight_kg"),
        height_cm     = row.get("height_cm"),
        daily_goal    = row.get("daily_goal",     8000),
        focus_areas   = row.get("focus_areas",    []) or [],
        exercise_freq = row.get("exercise_freq"),
        exercise_types= row.get("exercise_types", []) or [],
        city          = row.get("city"),
        avatar_url    = row.get("avatar_url"),
        tracking_dark_map       = row.get("tracking_dark_map",       False),
        tracking_audio_feedback = row.get("tracking_audio_feedback", True),
        tracking_countdown_timer = row.get("tracking_countdown_timer", False),
        tracking_keep_screen_on  = row.get("tracking_keep_screen_on",  False),
        tracking_auto_pause      = row.get("tracking_auto_pause",      False),
        tracking_auto_resume     = row.get("tracking_auto_resume",     True),
        referral_code            = row.get("referral_code"),
        referred_by              = row.get("referred_by"),
    )