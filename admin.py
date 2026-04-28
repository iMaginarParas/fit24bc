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

class FeedbackUpdate(BaseModel):
    is_approved: bool

# ── Endpoints ─────────────────────────────────────────────────────────────────

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

@router.get("/feedback")
async def get_feedback(request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    # Joining with user profile to get name
    resp = await client.get(f"{SUPABASE_URL}/rest/v1/feedback?select=*,user_profiles(name)", headers=_admin_headers())
    return resp.json()

@router.patch("/feedback/{fb_id}")
async def update_feedback(fb_id: str, body: FeedbackUpdate, request: Request):
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.patch(f"{SUPABASE_URL}/rest/v1/feedback?id=eq.{fb_id}", headers=_admin_headers(), json=body.dict())
    return resp.json()[0]
