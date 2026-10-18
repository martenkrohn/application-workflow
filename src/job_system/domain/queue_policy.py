"""Reversible discovery visibility and source ordering for routine vacancy work."""

from datetime import UTC, datetime, timedelta

BACKLOG_MAX_AGE_DAYS = 7
LOCATION_GROUPS = (
    "Primary",
    "Nearby",
    "Additional",
    "Other locations",
    "Unknown location",
)


def location_priority(locations: object) -> int:
    return 3 if locations else 4


def received_at(first_seen_at: str) -> datetime | None:
    try:
        scraped = datetime.fromisoformat(first_seen_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        scraped.replace(tzinfo=UTC)
        if scraped.tzinfo is None
        else scraped.astimezone(UTC)
    )


def source_priority(source: str) -> int:
    return 0


def older_than_backlog_window(first_seen_at: str, now: datetime) -> bool:
    """Age saved evidence, not publication dates or unchanged rediscovery timestamps."""
    scraped = received_at(first_seen_at)
    if scraped is None:
        return False
    return scraped < now - timedelta(days=BACKLOG_MAX_AGE_DAYS)
