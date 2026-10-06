from __future__ import annotations

from typing import Any

import redis


class RedisEvents:
    def __init__(self, url: str = "") -> None:
        self.url = url
        self.client = redis.Redis.from_url(url, decode_responses=True) if url else None

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        if self.client is not None:
            import json

            self.client.publish(channel, json.dumps(event))

    def health(self) -> dict[str, object]:
        if self.client is None:
            return {"configured": False, "connected": False}
        try:
            return {"configured": True, "connected": bool(self.client.ping())}
        except Exception as exc:
            return {"configured": True, "connected": False, "error": str(exc)}
