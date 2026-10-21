from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime:
    """Normalize SQLite naive timestamps and in-memory aware datetimes for comparison."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # A federal (government) employer. Marked by hand from a job's detail; its
    # jobs are no longer ingested for any persona.
    federal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobIndex(Base):
    """The one global job-id allocator and id -> table router.

    Each person has their own ``jobs__{persona_id}`` table (see
    ``app/db/job_tables.py``), but ``job.id`` must stay globally unique because
    it is used where no persona is in hand — the in-memory apply-session map, the
    ``/api/jobs/{job_id}`` routes, ``Application.job_id``, ``EventLog.job_id`` and
    the renumber healer. So an id is minted by inserting a row here first, then
    the per-persona row is written with that same id as a plain (non
    autoincrement) primary key. A lookup here answers "which table holds this id"
    in one indexed hit.
    """

    __tablename__ = "job_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    normalized_company: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobBase:
    """The column set every per-persona ``jobs__{persona_id}`` table shares.

    A plain mixin, not a mapped class: ``job_model(persona_id)`` builds one mapped
    subclass per persona from it (``app/db/job_tables.py``). SQLAlchemy copies each
    ``mapped_column`` into every subclass, and ``index=True`` names each index
    table-qualified (``ix_jobs__alex_mak_status``), so the eleven tables never
    collide. ``id`` is minted by :class:`JobIndex`, so autoincrement is off here.

    The row is self-contained: it folds in what used to live in the per-persona
    ``JobPackage`` (resume folder, ATS/credibility scores, applied/outcome) and
    the per-persona ``JobDisable`` (the tri-state ``enabled``). There is one
    ``status`` per row — the old split between a shared listing status and a
    per-persona package status is gone.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # Unique *within this table* — each person's list is numbered independently.
    seq_no: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # ISO-ish two-letter code derived from location at ingest. See job_country.py.
    country: Mapped[str] = mapped_column(String(2), default="US", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pay range read from the JD at ingest (services/salary_extract.py).
    salary: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="new", nullable=False, index=True)
    folder_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    staffing_firm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domains: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jd_fetched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Can this person credibly do this job? Scored from the JD alone.
    feasibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- folded from the old per-persona JobPackage -----------------------
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ats_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    credibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    credibility_findings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    # --- folded from the old per-persona JobDisable -----------------------
    # Tri-state: None = follow the automatic company-applied rule, True = forced
    # on (override the auto rule), False = turned off by hand.
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # The persona is the table, not a column. job_model stamps __persona_id__ on
    # each subclass; this read-only property lets the many `job.persona_id` reads
    # across the codebase keep working unchanged.
    @property
    def persona_id(self) -> str:
        return type(self).__persona_id__  # type: ignore[attr-defined]


# Type alias for annotations: a per-persona job row is some JobBase subclass.
JobRow = JobBase


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("persona_id", "normalized_company", name="uq_app_persona_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Points at a row in that persona's jobs table (ids are globally unique via
    # JobIndex). Nullable: a standalone company-applied record can outlive its job.
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="applied", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RejectedJob(Base):
    __tablename__ = "rejected_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), default="stage1", nullable=False)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AtsBoard(Base):
    """Career-page token we have searched (or will search) on Greenhouse/Lever/Ashby."""

    __tablename__ = "ats_boards"
    __table_args__ = (UniqueConstraint("source", "token", name="uq_ats_source_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedListing(Base):
    __tablename__ = "processed_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobSourceSeen(Base):
    __tablename__ = "job_source_seen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external"),
    )


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", nullable=False, index=True)
    persona_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seq_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seq_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
