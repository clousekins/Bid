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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company_rel")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seq_no: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # ISO-ish two-letter code derived from location at ingest. Boards do not
    # report a country, so this is inferred; a listing naming none is US, which
    # is why the column is never null. See services/job_country.py.
    country: Mapped[str] = mapped_column(String(2), default="US", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="new", nullable=False, index=True)
    folder_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    persona_id: Mapped[str] = mapped_column(String(128), default="phong_v_cao", nullable=False)
    staffing_firm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domains: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jd_fetched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Can the working persona credibly do this job? Scored from the JD alone,
    # before any resume is written, so weak matches can be skipped.
    feasibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    company_rel: Mapped[Company | None] = relationship(back_populates="jobs")
    packages: Mapped[list["JobPackage"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobPackage(Base):
    """Per-persona tailored resume package for a single job listing."""

    __tablename__ = "job_packages"
    __table_args__ = (
        UniqueConstraint("job_id", "persona_id", name="uq_job_package_persona"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    persona_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="resume_ready", nullable=False, index=True)
    folder_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # What the employer did next. updated_at cannot serve here — it moves on
    # every write, so response latency needs its own submission timestamp.
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )
    outcome_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Would this resume survive an ATS keyword filter for this posting?
    ats_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ats_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    # Does the document hold together as a truthful resume? Judged on the
    # resume's own text — dates against the tech it claims, not the prompt.
    credibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    credibility_findings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    job: Mapped[Job] = relationship(back_populates="packages")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("persona_id", "normalized_company", name="uq_app_persona_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
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
