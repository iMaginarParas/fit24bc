from fastapi import APIRouter, HTTPException, Depends
import os

router = APIRouter()

# In a real app, these would be in environment variables on Railway
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "AIzaSyCfCi-YoWWicYnFHKFxjkW6IpMHSltw4F8")

@router.get("/keys")
async def get_keys():
    """
    Returns public API keys needed by the frontend.
    """
    return {
        "google_maps": GOOGLE_MAPS_API_KEY,
        # Add other public keys here if needed
    }
