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
from dotenv import load_dotenv
import re

load_dotenv()  # loads .env file if present (local dev)

router = APIRouter()

# ── Config ───────────────────────────────────────────────────────────────────

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    import sys
    print("WARNING: SUPABASE_URL and SUPABASE_ANON_KEY are missing. Auth features will be disabled.", file=sys.stderr)

def _get_supabase_headers() -> dict:
    return {
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
    phone: str | None = Field(None, examples=["+919876543210"])
    email: str | None = Field(None, examples=["user@example.com"])
    mode: str = Field(
        "signup",
        description="'signup' for new users, 'login' for existing users",
        examples=["signup", "login"],
    )

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None: return None
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
    phone: str | None = Field(None, examples=["+919876543210"])
    email: str | None = Field(None, examples=["user@example.com"])
    token: str = Field(..., min_length=4, max_length=8, examples=["123456"])
    mode: str = Field(
        "signup",
        description="Must match the mode used in /send-otp",
        examples=["signup", "login"],
    )

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None: return None
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
    phone: str | None = None
    email: str | None = None


class GoogleSignRequest(BaseModel):
    id_token: str = Field(..., description="Google ID Token")


class VerifyOtpResponse(BaseModel):
    message: str
    user: UserProfile
    tokens: AuthTokens


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/send-otp",
    response_model=SendOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Send OTP (Email or Phone)",
)
async def send_otp(body: SendOtpRequest, request: Request):
    """
    Triggers OTP delivery. 
    - For Email: Uses Supabase (configure Resend in Supabase SMTP settings).
    - For Phone: Currently disabled/bypassed.
    """
    client: httpx.AsyncClient = request.app.state.http_client

    if body.email:
        # ── Email OTP Flow (Recommended: Config Resend in Supabase Dashboard) ──
        payload = {
            "email": body.email, 
            "create_user": body.mode == "signup"
        }
        url = f"{SUPABASE_URL}/auth/v1/otp"
        resp = await client.post(url, headers=_get_supabase_headers(), json=payload)
        if resp.status_code not in (200, 204):
            raise _supabase_error(resp)
        return SendOtpResponse(
            message="OTP sent successfully. Check your email.",
            phone=body.email, # Reusing field for convenience
        )

    if body.phone:
        # ── Phone OTP Flow (Currently Disabled) ──
        # To re-enable, uncomment the code below and ensure Twilio is configured.
        return SendOtpResponse(
            message="Phone OTP is currently disabled. Use email or Google.",
            phone=body.phone,
        )

    raise HTTPException(status_code=400, detail="Either email or phone is required")


@router.post(
    "/verify-otp",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify OTP and get session tokens",
)
async def verify_otp(body: VerifyOtpRequest, request: Request):
    """
    Verifies the 6-digit OTP received via Email or SMS.
    """
    client: httpx.AsyncClient = request.app.state.http_client

    if body.email:
        payload = {
            "email": body.email,
            "token": body.token,
            "type": "email",
        }
    elif body.phone:
        payload = {
            "phone": body.phone,
            "token": body.token,
            "type": "sms",
        }
    else:
        raise HTTPException(status_code=400, detail="Email or phone required")

    url = f"{SUPABASE_URL}/auth/v1/verify"
    resp = await client.post(url, headers=_get_supabase_headers(), json=payload)

    if resp.status_code != 200:
        raise _supabase_error(resp)

    data = resp.json()

    # Supabase returns tokens nested under "session" OR flat at root — handle both.
    sb_user    = data.get("user") or {}
    sb_session = data.get("session") or {}

    access_token  = sb_session.get("access_token")  or data.get("access_token")
    refresh_token_ = sb_session.get("refresh_token") or data.get("refresh_token")
    token_type    = sb_session.get("token_type")    or data.get("token_type", "bearer")
    expires_in    = sb_session.get("expires_in")    or data.get("expires_in", 3600)

    # user fields may also be at root when session is flat
    if not sb_user:
        sb_user = {"id": data.get("id", ""), "phone": data.get("phone", body.phone)}

    if not access_token or not sb_user.get("id"):
        import sys, json as _j
        print(f"[verify-otp] unexpected shape: {_j.dumps(data)[:400]}", file=sys.stderr)
        raise HTTPException(
            status_code=502,
            detail="Unexpected response from auth provider. Please try again.",
        )

    return VerifyOtpResponse(
        message="Verified successfully.",
        user=UserProfile(
            id=sb_user["id"],
            phone=sb_user.get("phone"),
            email=sb_user.get("email"),
        ),
        tokens=AuthTokens(
            access_token=access_token,
            refresh_token=refresh_token_,
            token_type=token_type,
            expires_in=expires_in,
        ),
    )


@router.post(
    "/google",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in with Google",
)
async def google_signin(body: GoogleSignRequest, request: Request):
    """
    Exchanges a Google ID Token for Supabase session tokens.
    """
    client: httpx.AsyncClient = request.app.state.http_client

    payload = {
        "id_token": body.id_token,
        "provider": "google",
    }

    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=id_token"
    resp = await client.post(url, headers=_get_supabase_headers(), json=payload)

    if resp.status_code != 200:
        raise _supabase_error(resp)

    data = resp.json()

    sb_user = data.get("user") or {}
    sb_session = data.get("session") or {}

    access_token = sb_session.get("access_token") or data.get("access_token")
    refresh_token_ = sb_session.get("refresh_token") or data.get("refresh_token")
    token_type = sb_session.get("token_type") or data.get("token_type", "bearer")
    expires_in = sb_session.get("expires_in") or data.get("expires_in", 3600)

    if not access_token or not sb_user.get("id"):
        raise HTTPException(
            status_code=502,
            detail="Unexpected response from auth provider.",
        )

    return VerifyOtpResponse(
        message="Google sign-in successful.",
        user=UserProfile(
            id=sb_user["id"],
            phone=sb_user.get("phone"),
            email=sb_user.get("email"),
        ),
        tokens=AuthTokens(
            access_token=access_token,
            refresh_token=refresh_token_,
            token_type=token_type,
            expires_in=expires_in,
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
    resp = await client.post(url, headers=_get_supabase_headers(), json=payload)

    if resp.status_code != 200:
        raise _supabase_error(resp)

    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data.get("expires_in", 3600),
        "token_type": data.get("token_type", "bearer"),
    }