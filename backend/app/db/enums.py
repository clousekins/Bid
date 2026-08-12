from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    NEW = "new"
    JD_READY = "jd_ready"
    SCAN_FAILED = "scan_failed"
    RESUME_READY = "resume_ready"
    APPLYING = "applying"
    APPLIED = "applied"
    EXPIRED = "expired"
    REJECTED_ONSITE = "rejected_onsite"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    NEEDS_MANUAL = "needs_manual"


class ApplySessionStatus(str, Enum):
    STARTING = "starting"
    FILLING = "filling"
    AWAITING_HUMAN = "awaiting_human"
    AWAITING_CONFIRM = "awaiting_confirm"
    SUBMITTING = "submitting"
    APPLIED = "applied"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_MANUAL = "needs_manual"


class PackageStatus(str, Enum):
    RESUME_READY = "resume_ready"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"


class BackgroundJobKind(str, Enum):
    DEEP_SCAN = "deep_scan"
    TAILOR = "tailor"


class BackgroundJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed job status transitions (from -> to). Empty set means terminal-ish flexibility.
JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.NEW: {
        JobStatus.JD_READY,
        JobStatus.SCAN_FAILED,
        JobStatus.SKIPPED,
        JobStatus.BLOCKED,
        JobStatus.EXPIRED,
        JobStatus.NEEDS_MANUAL,
        JobStatus.REJECTED_ONSITE,
    },
    JobStatus.JD_READY: {
        JobStatus.RESUME_READY,
        JobStatus.JD_READY,
        JobStatus.SCAN_FAILED,
        JobStatus.SKIPPED,
        JobStatus.BLOCKED,
        JobStatus.EXPIRED,
        JobStatus.NEEDS_MANUAL,
        JobStatus.REJECTED_ONSITE,
        JobStatus.APPLYING,
    },
    JobStatus.SCAN_FAILED: {
        JobStatus.NEW,
        JobStatus.JD_READY,
        JobStatus.SCAN_FAILED,
        JobStatus.SKIPPED,
        JobStatus.BLOCKED,
        JobStatus.EXPIRED,
        JobStatus.NEEDS_MANUAL,
        JobStatus.REJECTED_ONSITE,
    },
    JobStatus.RESUME_READY: {
        JobStatus.APPLYING,
        JobStatus.APPLIED,
        JobStatus.SKIPPED,
        JobStatus.BLOCKED,
        JobStatus.NEEDS_MANUAL,
        JobStatus.JD_READY,
        JobStatus.RESUME_READY,
    },
    JobStatus.APPLYING: {
        JobStatus.APPLIED,
        JobStatus.RESUME_READY,
        JobStatus.SKIPPED,
        JobStatus.NEEDS_MANUAL,
        JobStatus.BLOCKED,
    },
    JobStatus.APPLIED: set(),
    JobStatus.EXPIRED: {JobStatus.NEW, JobStatus.JD_READY, JobStatus.SCAN_FAILED},
    JobStatus.REJECTED_ONSITE: set(),
    JobStatus.SKIPPED: {
        JobStatus.NEW,
        JobStatus.JD_READY,
        JobStatus.SCAN_FAILED,
        JobStatus.RESUME_READY,
    },
    JobStatus.BLOCKED: set(),
    JobStatus.NEEDS_MANUAL: {
        JobStatus.JD_READY,
        JobStatus.RESUME_READY,
        JobStatus.APPLYING,
        JobStatus.APPLIED,
        JobStatus.SKIPPED,
        JobStatus.SCAN_FAILED,
    },
}


def can_transition(current: JobStatus | str, target: JobStatus | str) -> bool:
    cur = JobStatus(current) if not isinstance(current, JobStatus) else current
    tgt = JobStatus(target) if not isinstance(target, JobStatus) else target
    if cur == tgt:
        return True
    allowed = JOB_TRANSITIONS.get(cur)
    if allowed is None:
        return True
    if not allowed:
        return False
    return tgt in allowed
