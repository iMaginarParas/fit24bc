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
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_SERVICE_KEY", os.getenv("SUPABASE_ANON_KEY", "")))

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

# --- Helpers ---
async def _log_action(request: Request, action: str, target: str, details: dict = None):
    client: httpx.AsyncClient = request.app.state.http_client
    # In a real app, we'd get admin_id from auth token
    log_data = {
        "action": action,
        "target": target,
        "details": details or {}
    }
    await client.post(f"{SUPABASE_URL}/rest/v1/admin_logs", headers=_admin_headers(), json=log_data)

# --- Dashboard ---
@router.get("/dashboard")
async def get_dashboard_stats(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    from datetime import date, timedelta
    
    # Total Users & Points Earned
    users_resp = await client.get(f"{SUPABASE_URL}/rest/v1/user_profiles?select=points", headers=_admin_headers())
    users_data = users_resp.json() if users_resp.status_code == 200 else []
    total_users = len(users_data)
    total_points = sum((u.get("points") or 0) for u in users_data)
    
    # Daily Active
    today = date.today().isoformat()
    da_resp = await client.get(f"{SUPABASE_URL}/rest/v1/step_logs?log_date=eq.{today}&select=user_id", headers=_admin_headers())
    da_data = da_resp.json() if da_resp.status_code == 200 else []
    daily_active = len(set(d["user_id"] for d in da_data))
    
    # Line Chart: Last 7 Days user activity (number of step_logs per day)
    seven_days_ago = (date.today() - timedelta(days=6)).isoformat()
    line_resp = await client.get(f"{SUPABASE_URL}/rest/v1/step_logs?log_date=gte.{seven_days_ago}&select=log_date", headers=_admin_headers())
    line_data = line_resp.json() if line_resp.status_code == 200 else []
    
    # Aggregate by date
    from collections import Counter
    date_counts = Counter(d["log_date"] for d in line_data)
    
    labels = []
    line_chart_data = []
    for i in range(6, -1, -1):
        d = date.today() - timedelta(days=i)
        d_str = d.isoformat()
        labels.append(d.strftime("%a"))  # Mon, Tue, etc.
        line_chart_data.append(date_counts.get(d_str, 0))
        
    # Bar Chart: Activity Categories (Walking=0, Running=1, Cycling=2)
    bar_resp = await client.get(f"{SUPABASE_URL}/rest/v1/activity_sessions?select=type", headers=_admin_headers())
    bar_data = bar_resp.json() if bar_resp.status_code == 200 else []
    
    type_counts = Counter(s.get("type", 0) for s in bar_data)
    # Walking, Running, Cycling, Others
    bar_chart_data = [
        type_counts.get(0, 0),
        type_counts.get(1, 0),
        type_counts.get(2, 0),
        sum(count for t, count in type_counts.items() if t not in (0, 1, 2))
    ]
    
    return {
        "total_users": total_users,
        "daily_active": daily_active,
        "points_earned": total_points,
        "line_labels": labels,
        "line_data": line_chart_data,
        "bar_data": bar_chart_data
    }

# --- Categories ---
@router.get("/categories", response_model=List[Category])
async def get_categories(request: Request, search: Optional[str] = None):
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/categories?order=name"
    if search:
        url += f"&name=ilike.*{search}*"
    resp = await client.get(url, headers=_admin_headers())
    return resp.json()

@router.post("/categories")
async def add_category(cat: Category, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.post(f"{SUPABASE_URL}/rest/v1/categories", headers=_admin_headers(), json=cat.dict(exclude_none=True))
    data = resp.json()[0]
    await _log_action(request, "CREATE_CATEGORY", f"Category: {cat.name}", data)
    return data

@router.delete("/categories/{cat_id}")
async def delete_category(cat_id: str, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    await client.delete(f"{SUPABASE_URL}/rest/v1/categories?id=eq.{cat_id}", headers=_admin_headers())
    await _log_action(request, "DELETE_CATEGORY", f"ID: {cat_id}")
    return {"status": "deleted"}

# --- Tutorials ---
@router.get("/tutorials")
async def get_tutorials(request: Request, search: Optional[str] = None):
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/tutorials"
    if search:
        url += f"?title=ilike.*{search}*"
    resp = await client.get(url, headers=_admin_headers())
    return resp.json()

@router.post("/tutorials")
async def add_tutorial(tut: Tutorial, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.post(f"{SUPABASE_URL}/rest/v1/tutorials", headers=_admin_headers(), json=tut.dict(exclude_none=True))
    data = resp.json()[0]
    await _log_action(request, "CREATE_TUTORIAL", f"Tutorial: {tut.title}", data)
    return data

@router.delete("/tutorials/{tut_id}")
async def delete_tutorial(tut_id: str, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    await client.delete(f"{SUPABASE_URL}/rest/v1/tutorials?id=eq.{tut_id}", headers=_admin_headers())
    await _log_action(request, "DELETE_TUTORIAL", f"ID: {tut_id}")
    return {"status": "deleted"}

# --- Feedback ---
@router.get("/feedback")
async def get_feedback(request: Request, search: Optional[str] = None):
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/feedback?select=*,user_profiles(name)"
    if search:
        url += f"&message=ilike.*{search}*"
    resp = await client.get(url, headers=_admin_headers())
    return resp.json()

@router.patch("/feedback/{fb_id}")
async def update_feedback(fb_id: str, body: FeedbackUpdate, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.patch(f"{SUPABASE_URL}/rest/v1/feedback?id=eq.{fb_id}", headers=_admin_headers(), json=body.dict())
    data = resp.json()[0]
    await _log_action(request, "UPDATE_FEEDBACK", f"ID: {fb_id}", {"approved": body.is_approved})
    return data

@router.delete("/feedback/{fb_id}")
async def delete_feedback(fb_id: str, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    await client.delete(f"{SUPABASE_URL}/rest/v1/feedback?id=eq.{fb_id}", headers=_admin_headers())
    await _log_action(request, "DELETE_FEEDBACK", f"ID: {fb_id}")
    return {"status": "deleted"}

# --- Challenges ---
@router.get("/challenges")
async def get_challenges(request: Request, search: Optional[str] = None):
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/challenges"
    if search:
        url += f"?title=ilike.*{search}*"
    resp = await client.get(url, headers=_admin_headers())
    return resp.json()

@router.post("/challenges")
async def add_challenge(chal: Challenge, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.post(f"{SUPABASE_URL}/rest/v1/challenges", headers=_admin_headers(), json=chal.dict(exclude_none=True))
    data = resp.json()[0]
    await _log_action(request, "CREATE_CHALLENGE", f"Challenge: {chal.title}", data)
    return data

@router.delete("/challenges/{chal_id}")
async def delete_challenge(chal_id: str, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    await client.delete(f"{SUPABASE_URL}/rest/v1/challenges?id=eq.{chal_id}", headers=_admin_headers())
    await _log_action(request, "DELETE_CHALLENGE", f"ID: {chal_id}")
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
    await _log_action(request, "UPDATE_POINTS", f"User: {user_id}", {"new_points": body.points})
    return {"status": "updated", "points": body.points}

class BulkAction(BaseModel):
    user_ids: List[str]
    points: Optional[int] = None
    message: Optional[str] = None

@router.post("/users/bulk")
async def bulk_user_action(body: BulkAction, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    if body.points is not None:
        # Note: Ideally a single SQL query, but for REST we loop or use RPC
        for uid in body.user_ids:
            # First get current points
            p_resp = await client.get(f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{uid}&select=points", headers=_admin_headers())
            current = p_resp.json()[0].get("points", 0) if p_resp.json() else 0
            await client.patch(f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{uid}", headers=_admin_headers(), json={"points": current + body.points})
        await _log_action(request, "BULK_POINTS", f"{len(body.user_ids)} users", {"added": body.points})
    
    if body.message:
        # Broadcast logic for subset
        await _log_action(request, "BULK_BROADCAST", f"{len(body.user_ids)} users", {"msg": body.message})
        
    return {"status": "bulk_completed"}

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
    await _log_action(request, "UPDATE_CONFIG", f"Key: {body.key}", body.value)
    return {"status": "updated"}

@router.post("/broadcast")
async def send_broadcast(body: dict, request: Request):
    await _log_action(request, "BROADCAST", "All Users", {"msg": body.get("message")})
    return {"status": "broadcast_sent", "target": "all_users"}

# --- Activity Logs ---
@router.get("/logs")
async def get_admin_logs(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/admin_logs?order=created_at.desc&limit=50", headers=_admin_headers())
    return resp.json()
