"""
auth.py  –  Fit24 phone-OTP authentication
-------------------------------------------------
Flow
  Signup : POST /auth/send-otp   (type=signup)
           POST /auth/verify-otp (type=signup)  →  returns JWT + user
  Login  : POST /auth/send-otp   (type=login)
           POST /auth/verify-otp (type=login)   →  returns JWT + user

Supabase handles OTP generation & delivery via Twilio (configured in
your Supabase dashboard → Authentication → Phone).
"""

import os
from enum import Enum

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
import re

router = APIRouter()

# ── Config (set these in your environment / .env) ────────────────────────────

SUPABASE_URL: str = os.environ["SUPABASE_URL"]          # e.g. https://xxxx.supabase.co
SUPABASE_ANON_KEY: str = os.environ["SUPABASE_ANON_KEY"]  # public anon key

_SUPABASE_HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Content-Type": "application/json",
}

# ── Helpers ──────────────────────────────────────────────────────────────────

PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164 format


def _validate_e164(phone: str) -> str:
    """Ensure phone is E.164 (+919876543210)."""
    if not PHONE_RE.match(phone):
        raise ValueError(
            "Phone must be in E.164 format, e.g. +919876543210"
        )
    return phone


def _supabase_error(response: httpx.Response) -> HTTPException:
    """Parse Supabase error body and wrap in HTTPException."""
    try:
        detail = response.json().get("msg") or response.json().get("message") or response.text
    except Exception:
        detail = response.text
    return HTTPException(status_code=response.status_code, detail=detail)


# ── Schemas ──────────────────────────────────────────────────────────────────

class OtpType(str, Enum):
    signup = "signup"
    login  = "sms"   # Supabase uses "sms" for login OTP type


class SendOtpRequest(BaseModel):
    phone: str = Field(..., examples=["+919876543210"])
    mode: str = Field(
        "signup",
        description="'signup' for new users, 'login' for existing users",
        examples=["signup", "login"],
    )

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        return _validate_e164(v)

    @field_validator("mode")
    @classmethod
    def check_mode(cls, v: str) -> str:
        if v not in ("signup", "login"):
            raise ValueError("mode must be 'signup' or 'login'")
        return v


class SendOtpResponse(BaseModel):
    message: str
    phone: str


class VerifyOtpRequest(BaseModel):
    phone: str = Field(..., examples=["+919876543210"])
    token: str = Field(..., min_length=4, max_length=8, examples=["123456"])
    mode: str = Field(
        "signup",
        description="Must match the mode used in /send-otp",
        examples=["signup", "login"],
    )

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        return _validate_e164(v)

    @field_validator("mode")
    @classmethod
    def check_mode(cls, v: str) -> str:
        if v not in ("signup", "login"):
            raise ValueError("mode must be 'signup' or 'login'")
        return v


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserProfile(BaseModel):
    id: str
    phone: str


class VerifyOtpResponse(BaseModel):
    message: str
    user: UserProfile
    tokens: AuthTokens


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/send-otp",
    response_model=SendOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Send phone OTP (signup or login)",
)
async def send_otp(body: SendOtpRequest, request: Request):
    """
    Triggers Supabase to send a one-time password via Twilio SMS.

    - **signup** mode → creates a new user record (fails if phone already registered)
    - **login** mode  → sends OTP to existing user
    """
    client: httpx.AsyncClient = request.app.state.http_client

    if body.mode == "signup":
        # Supabase signup with phone — sends OTP automatically
        payload = {"phone": body.phone, "password": None, "channel": "sms"}
        url = f"{SUPABASE_URL}/auth/v1/otp"
    else:
        # Re-send OTP to existing user (login)
        payload = {"phone": body.phone, "channel": "sms"}
        url = f"{SUPABASE_URL}/auth/v1/otp"

    resp = await client.post(url, headers=_SUPABASE_HEADERS, json=payload)

    if resp.status_code not in (200, 204):
        raise _supabase_error(resp)

    return SendOtpResponse(
        message="OTP sent successfully. Check your SMS.",
        phone=body.phone,
    )


@router.post(
    "/verify-otp",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify OTP and get session tokens",
)
async def verify_otp(body: VerifyOtpRequest, request: Request):
    """
    Verifies the 6-digit OTP the user received via SMS.

    On success returns:
    - **access_token** (JWT) – use as `Authorization: Bearer <token>` on protected routes
    - **refresh_token** – use to obtain new access tokens when they expire
    - **user** – basic profile (id + phone)
    """
    client: httpx.AsyncClient = request.app.state.http_client

    # Supabase expects type = "sms" for both signup and login OTP verification
    payload = {
        "phone": body.phone,
        "token": body.token,
        "type": "sms",
    }

    url = f"{SUPABASE_URL}/auth/v1/verify"
    resp = await client.post(url, headers=_SUPABASE_HEADERS, json=payload)

    if resp.status_code != 200:
        raise _supabase_error(resp)

    data = resp.json()
    sb_user = data.get("user", {})
    sb_session = data.get("session", {})

    return VerifyOtpResponse(
        message="Phone verified successfully.",
        user=UserProfile(
            id=sb_user["id"],
            phone=sb_user.get("phone", body.phone),
        ),
        tokens=AuthTokens(
            access_token=sb_session["access_token"],
            refresh_token=sb_session["refresh_token"],
            token_type=sb_session.get("token_type", "bearer"),
            expires_in=sb_session.get("expires_in", 3600),
        ),
    )


@router.post(
    "/refresh-token",
    summary="Refresh expired access token",
)
async def refresh_token(refresh_token: str, request: Request):
    """Exchange a refresh_token for a new access_token."""
    client: httpx.AsyncClient = request.app.state.http_client

    payload = {"refresh_token": refresh_token}
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
    resp = await client.post(url, headers=_SUPABASE_HEADERS, json=payload)

    if resp.status_code != 200:
        raise _supabase_error(resp)

    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data.get("expires_in", 3600),
        "token_type": data.get("token_type", "bearer"),
    }