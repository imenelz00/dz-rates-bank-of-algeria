from decimal import Decimal

from . import config
from .sources.base import RateRecord


def validate_record(r: RateRecord) -> str | None:
    """Return a rejection reason, or None when the record is acceptable."""
    if r.rate_type not in config.RATE_TYPES:
        return f"unknown rate type {r.rate_type!r}"
    if r.value is None or not isinstance(r.value, Decimal) or not r.value.is_finite():
        return "value is not a finite number"
    lo, hi = config.PLAUSIBLE_RANGE
    if not (lo <= r.value <= hi):
        return f"value {r.value} outside plausible range {lo}-{hi}"
    if r.value_date < r.publication_date and r.granularity == "daily":
        return "value date earlier than quotation date"
    return None


def jump_reason(prev: Decimal, cur: Decimal) -> str | None:
    """Flag (but still store) suspicious day-over-day moves."""
    if prev and abs(cur - prev) / prev > Decimal(str(config.JUMP_THRESHOLD)):
        return f"moved {((cur - prev) / prev * 100):+.1f}% vs previous value ({prev})"
    return None
