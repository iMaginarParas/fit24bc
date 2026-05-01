"""
challenges.py – Challenge management and reward claiming
"""

import os
from datetime import datetime
from typing import List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter()

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")

def _user_headers(token: str) -> dict:
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

async def _get_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Token")
    token = auth.split(" ", 1)[1]
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(f"{SUPABASE_URL}/auth/v1/user", headers=_user_headers(token))
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid Token")
    return {"id": resp.json()["id"], "token": token}

# ── Schemas ──────────────────────────────────────────────────────────────────

class Challenge(BaseModel):
    id: str
    title: str
    description: str
    reward_coins: int
    requirement_type: str # 'steps', 'distance', 'calories'
    requirement_value: int
    is_daily: bool = True

class ClaimResponse(BaseModel):
    success: bool
    message: str
    new_balance: int

# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[Challenge])
async def list_challenges(request: Request, user: dict = Depends(_get_user)):
    """Fetch all available challenges."""
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{SUPABASE_URL}/rest/v1/challenges?select=*"
    resp = await client.get(url, headers=_user_headers(user["token"]))
    if resp.status_code != 200:
        return [] # Fallback to empty if table doesn't exist yet
    return resp.json()

@router.post("/claim/{challenge_id}", response_model=ClaimResponse)
async def claim_reward(challenge_id: str, request: Request, user: dict = Depends(_get_user)):
    """Verify challenge completion and award coins."""
    client: httpx.AsyncClient = request.app.state.http_client
    
    # 1. Get Challenge Details
    c_url = f"{SUPABASE_URL}/rest/v1/challenges?id=eq.{challenge_id}&limit=1"
    c_resp = await client.get(c_url, headers=_user_headers(user["token"]))
    if c_resp.status_code != 200 or not c_resp.json():
        raise HTTPException(status_code=404, detail="Challenge not found")
    challenge = c_resp.json()[0]

    # 2. Check if already claimed today
    today = datetime.now().strftime("%Y-%m-%d")
    claim_check_url = f"{SUPABASE_URL}/rest/v1/user_claims?user_id=eq.{user['id']}&challenge_id=eq.{challenge_id}&date=eq.{today}"
    cc_resp = await client.get(claim_check_url, headers=_user_headers(user["token"]))
    if cc_resp.status_code == 200 and cc_resp.json():
        raise HTTPException(status_code=400, detail="Already claimed today")

    # 3. Verify Requirement (Check user's daily stats)
    stats_url = f"{SUPABASE_URL}/rest/v1/step_logs?user_id=eq.{user['id']}&log_date=eq.{today}&limit=1"
    s_resp = await client.get(stats_url, headers=_user_headers(user["token"]))
    user_steps = 0
    if s_resp.status_code == 200 and s_resp.json():
        user_steps = s_resp.json()[0].get("steps", 0)

    if user_steps < challenge["requirement_value"]:
        raise HTTPException(status_code=400, detail=f"Requirement not met. You need {challenge['requirement_value']} steps.")

    # 4. Award Coins (Update user_profiles)
    profile_url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user['id']}"
    p_resp = await client.get(profile_url, headers=_user_headers(user["token"]))
    current_coins = p_resp.json()[0].get("points", 0) if p_resp.json() else 0
    new_coins = current_coins + challenge["reward_coins"]

    await client.patch(profile_url, headers=_user_headers(user["token"]), json={"points": new_coins})

    # 5. Record Claim
    await client.post(f"{SUPABASE_URL}/rest/v1/user_claims", headers=_user_headers(user["token"]), json={
        "user_id": user["id"],
        "challenge_id": challenge_id,
        "date": today,
        "reward": challenge["reward_coins"]
    })

    return ClaimResponse(success=True, message="Reward claimed!", new_balance=new_coins)
