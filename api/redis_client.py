import os

from upstash_redis.asyncio import Redis

redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
)


async def init_redis() -> None:
    """Verify the Upstash Redis connection on startup."""
    pong = await redis.ping()
    assert pong == "PONG", f"Upstash Redis connection failed: {pong}"
    print("Upstash Redis connected")
