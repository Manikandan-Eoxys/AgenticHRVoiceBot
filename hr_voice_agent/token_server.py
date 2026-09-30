"""
Simple LiveKit Token Server

Runs on:
http://localhost:8000

Frontend requests:

GET /getToken?name=Ravi

Returns:

{
    "token": "xxxxx"
}
"""

import uuid

from fastapi import FastAPI, HTTPException
from livekit import api

from fastapi.middleware.cors import CORSMiddleware

from config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    WORKER_AGENT_NAME,
)
from security.call_blocklist import is_number_blocked
from routers.monitor_router import router as monitor_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(monitor_router)


@app.get("/getToken")
async def get_token(name: str):
    if is_number_blocked(name):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: Identity/number '{name}' is in the blocked list.",
        )

    # Fresh unique room per session — guarantees agent is always dispatched
    room_name = f"hr-room-{uuid.uuid4().hex[:8]}"

    token = (
        api.AccessToken(
            LIVEKIT_API_KEY,
            LIVEKIT_API_SECRET,
        )
        .with_identity(name)
        .with_name(name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        # ------------------------------------------------------------------
        # This tells LiveKit Cloud to automatically dispatch the agent worker
        # to this room when the first participant joins.
        # ------------------------------------------------------------------
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(agent_name=WORKER_AGENT_NAME)
                ]
            )
        )
        .to_jwt()
    )

    return {
        "token": token,
        "room": room_name,
        "ws_url": LIVEKIT_URL,
    }

@app.get("/getSipToken")
async def get_sip_token(name: str):
    """
    Returns a token for the fixed SIP room 'hr-sip-live'.

    The SIP dispatch rule (setup_sip.py) routes every MicroSIP call into
    this same room. When the browser fetches a token from here and joins,
    both the MicroSIP caller and the browser are in the same LiveKit room,
    so TranscriptionReceived events fire correctly and the transcript is
    visible in the UI.
    """
    if is_number_blocked(name):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: Identity/number '{name}' is in the blocked list.",
        )

    room_name = "hr-sip-live"   # must match setup_sip.py SIP_ROOM

    token = (
        api.AccessToken(
            LIVEKIT_API_KEY,
            LIVEKIT_API_SECRET,
        )
        .with_identity(name)
        .with_name(name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    return {
        "token": token,
        "room": room_name,
        "ws_url": LIVEKIT_URL,
    }