from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int  # whole requests still available right now
    retry_after: float  # seconds until a request would be allowed (0 when allowed)
