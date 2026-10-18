from datetime import datetime, timezone


def utc_from_monotonic(
    monotonic_value: float, anchor_monotonic: float, anchor_utc: datetime
) -> str:
    delta = monotonic_value - anchor_monotonic
    return datetime.fromtimestamp(
        anchor_utc.timestamp() + delta, timezone.utc
    ).isoformat()
