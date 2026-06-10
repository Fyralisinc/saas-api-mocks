"""Ashby entity JSON shapes (candidate / application / job / interview / offer).

Built from Ashby's first-party OpenAPI (developers.ashbyhq.com). The builders
assemble each entity's full wire object once (the seed stores the result verbatim
in ``app_ashby.entities.data``; the app returns it unchanged). Two wire facts:

  * **Timestamps are ISO-8601 UTC with MILLISECOND precision + ``Z``** (e.g.
    ``2024-01-15T10:30:00.000Z``).
  * **Enum casing is Capitalized** — application ``status`` ∈ {Hired, Archived,
    Active, Lead}; email/phone ``type`` ∈ {Personal, Work, Other}; etc. (Lowercase
    forms some third-party blogs show are wrong.)

Note which entities carry top-level timestamps: candidate / application / job have
``createdAt`` + ``updatedAt``; **interview** (an interview *type* definition) and
**offer** do NOT — offer exposes ``decidedAt`` + a nested ``latestVersion.createdAt``
instead. The ``app_ashby.entities.created_at``/``updated_at`` COLUMNS are internal
ordering/sync keys and are independent of whether the wire object exposes them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# ---- RPC categories Fyralis shards (one ``ashby_entity`` shard per type) ----
CATEGORIES = ("candidate", "application", "job", "interview", "offer")

# ---- Wire enums (for the seed + tests + the blind slice's structural checks) ----
APPLICATION_STATUSES = {"Hired", "Archived", "Active", "Lead"}
JOB_STATUSES = {"Draft", "Open", "Closed", "Archived"}
EMPLOYMENT_TYPES = {"FullTime", "PartTime", "Intern", "Contract", "Temporary"}
OFFER_ACCEPTANCE_STATUSES = {
    "Accepted", "Declined", "Pending", "Created", "Cancelled", "WaitingOnResponse",
}
OFFER_STATUSES = {
    "WaitingOnApprovalStart", "WaitingOnOfferApproval", "WaitingOnApprovalDefinition",
    "WaitingOnCandidateResponse", "CandidateRejected", "CandidateAccepted",
    "OfferCancelled",
}
EMAIL_TYPES = {"Personal", "Work", "Other"}
SOCIAL_TYPES = {
    "LinkedIn", "GitHub", "Twitter", "Medium", "StackOverflow", "YouTube",
    "CodePen", "Website",
}

# Lifecycle statuses that mean the recruiting funnel reached a terminal state — the
# `state_change` discriminator Fyralis keys off (`status`/`offerStatus`/`stage`).
# Stored as the denormalised `status` column so a terminal entity is identifiable.
TERMINAL_STATUSES = {
    "Hired", "Archived", "Closed", "Accepted", "Declined", "Cancelled",
    "CandidateAccepted", "CandidateRejected", "OfferCancelled",
}


def iso_ms(dt: Optional[datetime]) -> Optional[str]:
    """ISO-8601 UTC, millisecond precision, ``Z`` suffix — Ashby's date-time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _email(value: str, etype: str = "Work", primary: bool = True) -> dict[str, Any]:
    return {"value": value, "type": etype, "isPrimary": primary}


def _phone(value: str, ptype: str = "Mobile", primary: bool = True) -> dict[str, Any]:
    return {"value": value, "type": ptype, "isPrimary": primary}


def candidate_dto(
    *,
    entity_id: str,
    name: str,
    email: str,
    phone: str,
    created_at: datetime,
    updated_at: datetime,
    application_ids: list[str],
    social_links: list[dict[str, Any]],
    tags: list[dict[str, Any]],
    position: Optional[str],
    company: Optional[str],
    school: Optional[str],
    profile_url: str,
    source: Optional[dict[str, Any]],
    timezone_name: Optional[str] = None,
) -> dict[str, Any]:
    primary_email = _email(email)
    primary_phone = _phone(phone)
    return {
        "id": entity_id,
        "createdAt": iso_ms(created_at),
        "updatedAt": iso_ms(updated_at),
        "name": name,
        "primaryEmailAddress": primary_email,
        "emailAddresses": [primary_email],
        "primaryPhoneNumber": primary_phone,
        "phoneNumbers": [primary_phone],
        "socialLinks": social_links,
        "tags": tags,
        "position": position,
        "company": company,
        "school": school,
        "applicationIds": application_ids,
        "profileUrl": profile_url,
        "source": source,
        "timezone": timezone_name,
    }


def application_dto(
    *,
    entity_id: str,
    created_at: datetime,
    updated_at: datetime,
    status: str,
    candidate_ref: dict[str, Any],
    current_stage: dict[str, Any],
    job_ref: dict[str, Any],
    source: Optional[dict[str, Any]],
    hiring_team: list[dict[str, Any]],
    archive_reason: Optional[dict[str, Any]] = None,
    archived_at: Optional[datetime] = None,
    applied_via_job_posting_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "createdAt": iso_ms(created_at),
        "updatedAt": iso_ms(updated_at),
        "status": status,
        "customFields": [],
        "candidate": candidate_ref,
        "currentInterviewStage": current_stage,
        "source": source,
        "archiveReason": archive_reason,
        "archivedAt": iso_ms(archived_at),
        "job": job_ref,
        "creditedToUser": None,
        "hiringTeam": hiring_team,
        "appliedViaJobPostingId": applied_via_job_posting_id,
    }


def job_dto(
    *,
    entity_id: str,
    title: str,
    status: str,
    employment_type: str,
    location_id: str,
    department_id: str,
    default_interview_plan_id: str,
    interview_plan_ids: list[str],
    job_posting_ids: list[str],
    hiring_team: list[dict[str, Any]],
    created_at: datetime,
    updated_at: datetime,
    opened_at: Optional[datetime],
    closed_at: Optional[datetime],
    confidential: bool = False,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "title": title,
        "confidential": confidential,
        "status": status,
        "employmentType": employment_type,
        "locationId": location_id,
        "departmentId": department_id,
        "defaultInterviewPlanId": default_interview_plan_id,
        "interviewPlanIds": interview_plan_ids,
        "customFields": [],
        "jobPostingIds": job_posting_ids,
        "customRequisitionId": None,
        "brandId": None,
        "hiringTeam": hiring_team,
        "createdAt": iso_ms(created_at),
        "updatedAt": iso_ms(updated_at),
        "openedAt": iso_ms(opened_at),
        "closedAt": iso_ms(closed_at),
    }


def interview_dto(
    *,
    entity_id: str,
    title: str,
    job_id: str,
    feedback_form_definition_id: str,
    is_archived: bool = False,
    is_debrief: bool = False,
    is_feedback_required: bool = True,
    is_feedback_requested: bool = False,
    instructions_plain: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "title": title,
        "externalTitle": title,
        "isArchived": is_archived,
        "isDebrief": is_debrief,
        "isFeedbackRequired": is_feedback_required,
        "isFeedbackRequested": is_feedback_requested,
        "instructionsHtml": (f"<p>{instructions_plain}</p>" if instructions_plain else None),
        "instructionsPlain": instructions_plain,
        "jobId": job_id,
        "feedbackFormDefinitionId": feedback_form_definition_id,
    }


def offer_dto(
    *,
    entity_id: str,
    application_id: str,
    acceptance_status: str,
    offer_status: str,
    decided_at: Optional[datetime],
    version_created_at: datetime,
    start_date: Optional[str],
    salary: Optional[dict[str, Any]] = None,
    opening_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "decidedAt": iso_ms(decided_at),
        "applicationId": application_id,
        "acceptanceStatus": acceptance_status,
        "offerStatus": offer_status,
        "latestVersion": {
            "id": entity_id + "-v1",
            "startDate": start_date,
            "salary": salary,
            "createdAt": iso_ms(version_created_at),
            "openingId": opening_id,
            "customFields": [],
            "fileHandles": [],
            "author": None,
            "approvalStatus": None,
        },
    }
