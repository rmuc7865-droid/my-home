from __future__ import annotations

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5


def market_bar_record_id(
    *,
    provider: str,
    system: str,
    ticker: str,
    multiplier: int,
    timespan: str,
    timestamp: datetime,
    variant: str = "",
) -> UUID:
    """Return a stable UUID for one provider market aggregate bar.

    Re-collecting the same ticker/interval/timestamp produces the same UUID,
    allowing the API's existing unique ``record_id`` constraint to reject
    retries without creating duplicate measurement rows.
    """
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")

    timestamp_utc = timestamp.astimezone(timezone.utc)
    identity = "|".join(
        (
            provider.strip().lower(),
            system.strip().lower(),
            ticker.strip().upper(),
            str(int(multiplier)),
            timespan.strip().lower(),
            timestamp_utc.isoformat(),
            variant.strip().lower(),
        )
    )
    return uuid5(NAMESPACE_URL, f"home-monitor-market-bar:{identity}")
