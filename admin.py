"""
admin.py – Admin management for Fit24
--------------------------------------
SQL Schema (run in Supabase SQL editor):

create table if not exists public.categories (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  icon_name text,
  is_active boolean default true,
  created_at timestamptz default now()
);

create table if not exists public.tutorials (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  video_url text not null,
  thumbnail_url text,
  category_id uuid references public.categories(id),
  upvotes int default 0,
  downvotes int default 0,
  created_at timestamptz default now()
);

create table if not exists public.feedback (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id),
  message text not null,
  is_approved boolean default false,
  created_at timestamptz default now()
);
"""

import os
from typing import List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY", ""))

def _admin_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# ── Schemas ──────────────────────────────────────────────────────────────────

class Category(BaseModel):
    id: Optional[str] = None
    name: str
    icon_name: Optional[str] = None
    is_active: bool = True

class Tutorial(BaseModel):
    id: Optional[str] = None
    title: str
    video_url: str
    thumbnail_url: Optional[str] = None
    category_id: Optional[str] = None

class Challenge(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = None
    reward_coins: int
    requirement_type: str
    requirement_value: int
    is_daily: bool = True

class FeedbackUpdate(BaseModel):
    is_approved: bool

class PointUpdate(BaseModel):
    points: int

class ConfigUpdate(BaseModel):
    key: str
    value: dict

# ── Endpoints ─────────────────────────────────────────────────────────────────

# --- Categories ---
@router.get("/categories", response_model=List[Category])
async def get_categories(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/categories?order=name", headers=_admin_headers())
    return resp.json()

@router.post("/categories")
async def add_category(cat: Category, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.post(f"{SUPABASE_URL}/rest/v1/categories", headers=_admin_headers(), json=cat.dict(exclude_none=True))
    return resp.json()[0]

@router.delete("/categories/{cat_id}")
async def delete_category(cat_id: str, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    await client.delete(f"{SUPABASE_URL}/rest/v1/categories?id=eq.{cat_id}", headers=_admin_headers())
    return {"status": "deleted"}

# --- Tutorials ---
@router.get("/tutorials")
async def get_tutorials(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/tutorials", headers=_admin_headers())
    return resp.json()

@router.post("/tutorials")
async def add_tutorial(tut: Tutorial, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.post(f"{SUPABASE_URL}/rest/v1/tutorials", headers=_admin_headers(), json=tut.dict(exclude_none=True))
    return resp.json()[0]

# --- Feedback ---
@router.get("/feedback")
async def get_feedback(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/feedback?select=*,user_profiles(name)", headers=_admin_headers())
    return resp.json()

@router.patch("/feedback/{fb_id}")
async def update_feedback(fb_id: str, body: FeedbackUpdate, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.patch(f"{SUPABASE_URL}/rest/v1/feedback?id=eq.{fb_id}", headers=_admin_headers(), json=body.dict())
    return resp.json()[0]

# --- Challenges ---
@router.get("/challenges")
async def get_challenges(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/challenges", headers=_admin_headers())
    return resp.json()

@router.post("/challenges")
async def add_challenge(chal: Challenge, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.post(f"{SUPABASE_URL}/rest/v1/challenges", headers=_admin_headers(), json=chal.dict(exclude_none=True))
    return resp.json()[0]

@router.delete("/challenges/{chal_id}")
async def delete_challenge(chal_id: str, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    await client.delete(f"{SUPABASE_URL}/rest/v1/challenges?id=eq.{chal_id}", headers=_admin_headers())
    return {"status": "deleted"}

# --- Users & Points ---
@router.get("/users")
async def get_users(request: Request, search: Optional[str] = None):
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?select=*"
    if search:
        url += f"&name=ilike.*{search}*"
    resp = await client.get(url, headers=_admin_headers())
    return resp.json()

@router.patch("/users/{user_id}/points")
async def update_user_points(user_id: str, body: PointUpdate, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.patch(f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user_id}", headers=_admin_headers(), json={"points": body.points})
    return {"status": "updated", "points": body.points}

# --- Referrals ---
@router.get("/referrals")
async def get_referral_stats(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/user_profiles?select=id,name,referral_code,referred_by", headers=_admin_headers())
    profiles = resp.json()
    stats = []
    for p in profiles:
        if not p.get('referral_code'): continue
        count = sum(1 for other in profiles if other.get('referred_by') == p['referral_code'])
        if count > 0:
            stats.append({
                "user_id": p['id'],
                "name": p['name'] or "Anonymous",
                "code": p['referral_code'],
                "count": count
            })
    return sorted(stats, key=lambda x: x['count'], reverse=True)

# --- System Config & Broadcast ---
@router.get("/config")
async def get_system_config(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/system_config", headers=_admin_headers())
    if resp.status_code != 200: return []
    return resp.json()

@router.patch("/config")
async def update_system_config(body: ConfigUpdate, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.patch(f"{SUPABASE_URL}/rest/v1/system_config?key=eq.{body.key}", headers=_admin_headers(), json={"value": body.value})
    return {"status": "updated"}

@router.post("/broadcast")
async def send_broadcast(body: dict, request: Request):
    return {"status": "broadcast_sent", "target": "all_users"}
