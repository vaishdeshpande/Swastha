import json
import os

from fastapi import APIRouter, HTTPException

from livekit.api import AccessToken, VideoGrants

from api.schemas import TokenRequest, TokenResponse

router = APIRouter()

LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Issue a LiveKit room token",
    description="""
Generate a short-lived LiveKit JWT for a patient or staff participant to join a voice room.

The frontend calls this before connecting to the LiveKit room. The agent worker
is already listening in the same room — it starts speaking as soon as the patient joins.

**Token grants**: `room_join`, `can_update_own_metadata`
""",
    responses={
        200: {"description": "JWT issued successfully"},
        500: {"description": "LiveKit credentials missing from environment"},
    },
)
async def get_token(body: TokenRequest) -> TokenResponse:
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.identity = body.participant_name
    # Embed preferred_lang in participant metadata so the LiveKit agent can read
    # it synchronously at join time — before any utterance is transcribed.
    token = (
        token
        .with_name(body.participant_name)
        .with_metadata(json.dumps({"preferred_lang": body.preferred_lang}))
        .with_grants(VideoGrants(room_join=True, room=body.room_name, can_update_own_metadata=True))
    )
    return TokenResponse(token=token.to_jwt())
