"""Validation + idempotent upsert with revision history ("overwrite-with-history")."""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ExchangeRate, RateRevision, utcnow
from .sources.base import RateRecord
from .validation import jump_reason, validate_record

Q = Decimal("0.000001")


@dataclass
class UpsertResult:
    found: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    flagged: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.found} found, {self.inserted} new, {self.updated} corrected, {self.unchanged} unchanged, "
                f"{self.rejected} rejected, {self.flagged} flagged")


def upsert_records(session: Session, records: list[RateRecord]) -> UpsertResult:
    res = UpsertResult(found=len(records))
    groups: dict[tuple, list[RateRecord]] = defaultdict(list)
    for r in records:
        r.value = r.value.quantize(Q) if isinstance(r.value, Decimal) and r.value.is_finite() else r.value
        err = validate_record(r)
        if err:
            res.rejected += 1
            if len(res.errors) < 20:
                res.errors.append(f"{r.currency_code} {r.rate_type} {r.publication_date}: {err}")
            continue
        groups[(r.currency_code, r.rate_type)].append(r)

    now = utcnow()
    for (cur, rt), recs in groups.items():
        recs.sort(key=lambda x: (x.publication_date, x.value_date))
        lo, hi = recs[0].publication_date, recs[-1].publication_date
        existing = {(row.publication_date, row.value_date): row for row in session.scalars(
            select(ExchangeRate).where(ExchangeRate.currency_code == cur, ExchangeRate.rate_type == rt,
                                       ExchangeRate.publication_date.between(lo, hi)))}
        prev = None
        if recs[0].granularity == "daily":
            row = session.scalars(select(ExchangeRate).where(
                ExchangeRate.currency_code == cur, ExchangeRate.rate_type == rt,
                ExchangeRate.publication_date < lo).order_by(ExchangeRate.publication_date.desc()).limit(1)).first()
            prev = row.value if row else None
        for r in recs:
            reason = jump_reason(prev, r.value) if (r.granularity == "daily" and prev is not None) else None
            prev = r.value
            key = (r.publication_date, r.value_date)
            row = existing.get(key)
            if row is None:
                row = ExchangeRate(currency_code=cur, rate_type=rt, granularity=r.granularity, value=r.value,
                                   publication_date=r.publication_date, value_date=r.value_date, retrieved_at=now,
                                   source_url=r.source_url, source_kind=r.source_kind, raw_ref=r.raw_ref,
                                   flagged=bool(reason), flag_reason=reason)
                session.add(row)
                existing[key] = row
                res.inserted += 1
            elif row.value != r.value:
                session.flush()
                session.add(RateRevision(rate_id=row.id, old_value=row.value, new_value=r.value, changed_at=now))
                row.value, row.retrieved_at, row.raw_ref = r.value, now, r.raw_ref
                row.flagged, row.flag_reason = bool(reason), reason
                res.updated += 1
            else:
                res.unchanged += 1
                continue
            if reason:
                res.flagged += 1
    session.flush()
    return res
