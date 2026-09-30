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
    # Set by hand, and the only status that says so. A rejected listing stays
    # in the list and stays readable, but it is out of the pipeline: no batch
    # picks it up, and it no longer holds its company against the next job
    # that comes from there. Distinct from REJECTED_ONSITE, which is a gate's
    # verdict on the location, and from ApplicationOutcome.REJECTED, which is
    # the employer's answer after applying.
    REJECTED = "rejected"
    # Set by hand once an employer takes the application further. Like
    # REJECTED it is a person recording what happened outside the tool, so it
    # is reachable from any status: an application made by hand may never have
    # passed through APPLIED here. Distinct from ApplicationOutcome.INTERVIEW,
    # which records the same event against one persona's package; this is the
    # listing's own stage, and it is what the Pipeline filter shows.
    INTERVIEW = "interview"
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
    JobStatus.APPLIED: {JobStatus.INTERVIEW},
    # Undoing an interview puts the listing back where the pipeline can see it,
    # the way undoing a rejection does. APPLIED stays reachable for the case
    # where the application really did go through this tool. The employer's
    # verdict belongs on the package as an ApplicationOutcome, not here.
    JobStatus.INTERVIEW: {
        JobStatus.APPLIED,
        JobStatus.JD_READY,
        JobStatus.NEW,
        JobStatus.SCAN_FAILED,
    },
    JobStatus.EXPIRED: {JobStatus.NEW, JobStatus.JD_READY, JobStatus.SCAN_FAILED},
    JobStatus.REJECTED_ONSITE: set(),
    # Undoing a rejection puts the listing back where the pipeline can see it.
    # Without a way back, one misplaced click would need a database edit.
    JobStatus.REJECTED: {
        JobStatus.NEW,
        JobStatus.JD_READY,
        JobStatus.SCAN_FAILED,
    },
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
    # Rejecting is a person overruling the pipeline, so it is reachable from
    # anywhere — including the statuses that are otherwise terminal. Listing it
    # in each of the eleven sets above would say the same thing eleven times,
    # and would be one edit away from being wrong for a new status.
    # Marking an interview is the same kind of act: a person recording what an
    # employer did, for an application that may have been made outside this
    # tool and so never reached APPLIED here.
    if tgt in {JobStatus.REJECTED, JobStatus.INTERVIEW}:
        return True
    allowed = JOB_TRANSITIONS.get(cur)
    if allowed is None:
        return True
    if not allowed:
        return False
    return tgt in allowed
