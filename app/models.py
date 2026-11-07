from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp (portable across SQLite/PostgreSQL)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Currency(Base):
    
    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    currency_code: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    rate_type: Mapped[str] = mapped_column(String(16))      # reference|buy|sell|annual_avg|annual_eop
    granularity: Mapped[str] = mapped_column(String(8))     # daily|annual
    value: Mapped[Decimal] = mapped_column(Numeric(14, 6))  # DZD per 1 unit; NUMERIC, never float
    publication_date: Mapped[date] = mapped_column(Date)    # quotation / publication date
    value_date: Mapped[date] = mapped_column(Date)          # equals publication_date when the source has none
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source_url: Mapped[str] = mapped_column(String(512))
    source_kind: Mapped[str] = mapped_column(String(32))    # mock_reference|boa_reference|boa_quotation|boa_annual_xlsx...
    raw_ref: Mapped[str | None] = mapped_column(String(128))  # hash of the raw artifact, for audit/re-parse
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("currency_code", "rate_type", "publication_date", "value_date", name="uq_rate_key"),
        Index("ix_rate_lookup", "currency_code", "rate_type", "publication_date"),
    )


class RateRevision(Base):
    """History of corrections: written whenever an already stored value changes."""
    __tablename__ = "rate_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    rate_id: Mapped[int] = mapped_column(ForeignKey("exchange_rates.id"))
    old_value: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    new_value: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(12))        # scheduled|manual|backfill|retry
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|success|partial|failed
    records_found: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list["SyncRunItem"]] = relationship(back_populates="run", order_by="SyncRunItem.id")


class SyncRunItem(Base):
    """One row per source x currency inside a run, so a partial run is explainable and retryable."""
    __tablename__ = "sync_run_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"))
    source_kind: Mapped[str] = mapped_column(String(32))
    currency_code: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10))         # success|partial|failed
    records_found: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text)
    run: Mapped[SyncRun] = relationship(back_populates="items")
