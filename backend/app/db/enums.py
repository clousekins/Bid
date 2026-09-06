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


class ApplicationOutcome(str, Enum):
    """What the employer did after we applied.

    Applying is not the outcome. Without this the funnel ends at "sent" and
    there is no signal to tell a resume that works from one that does not.
    """

    PENDING = "pending"          # submitted, nothing back yet
    ACKNOWLEDGED = "acknowledged"  # auto-reply / received confirmation only
    SCREEN = "screen"            # recruiter screen or take-home
    INTERVIEW = "interview"      # real interview scheduled or held
    OFFER = "offer"
    REJECTED = "rejected"        # employer said no
    GHOSTED = "ghosted"          # no reply, written off


# Outcomes that count as the employer engaging with a human.
RESPONSE_OUTCOMES: frozenset[str] = frozenset(
    {
        ApplicationOutcome.SCREEN.value,
        ApplicationOutcome.INTERVIEW.value,
        ApplicationOutcome.OFFER.value,
    }
)

# Outcomes that are settled — no further reply expected.
TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {
        ApplicationOutcome.OFFER.value,
        ApplicationOutcome.REJECTED.value,
        ApplicationOutcome.GHOSTED.value,
    }
)


class BackgroundJobKind(str, Enum):
    DEEP_SCAN = "deep_scan"
    TAILOR = "tailor"
    DISCOVER = "discover"
    DISCOVER_COMPANIES = "discover_companies"
    DISCOVER_FRESH = "discover_fresh"
    DISCOVER_SITE = "discover_site"


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
