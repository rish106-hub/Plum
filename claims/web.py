"""Local reviewer interface and durable upload workflow for OPD claims."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from claims.agent_pipeline import (
    adjudicate_handoff,
    document_evidence_trace,
    resolve_document_handoff,
)
from claims.ai_review import resolve_evidence
from claims.core import claim_provider, evaluate_claim
from claims.documents import (
    BILL_TYPES,
    normal_name,
    parse_document_date,
    process_uploads,
    sniff_media_type,
)
from claims.money import has_subpaise_precision, to_paise, to_rupees
from claims.policy import PolicyConfigurationError, load_policy, policy_fingerprint

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")
MAX_FILES = 6
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
CATEGORIES = (
    "CONSULTATION",
    "DIAGNOSTIC",
    "PHARMACY",
    "DENTAL",
    "VISION",
    "ALTERNATIVE_MEDICINE",
)


DEMO_CLOCK_ENVIRONMENTS = frozenset({"development", "test"})
# The policy is issued and administered in India, so a claim's submission date is
# the calendar date in this timezone, not the UTC date of the server clock.
DEFAULT_POLICY_TIMEZONE = "Asia/Kolkata"
_IST_FALLBACK = timezone(timedelta(hours=5, minutes=30), "IST")
logger = logging.getLogger("claims.web")


def _policy_timezone() -> tzinfo:
    """Timezone for submission dates: PLUM_POLICY_TIMEZONE (IANA name), default Asia/Kolkata."""
    name = os.getenv("PLUM_POLICY_TIMEZONE", "").strip() or DEFAULT_POLICY_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        if name == DEFAULT_POLICY_TIMEZONE:
            return _IST_FALLBACK  # host without a tz database; India observes no DST
        raise RuntimeError(f"PLUM_POLICY_TIMEZONE={name!r} is not a known IANA timezone name") from exc


def _local_date(moment: datetime) -> str:
    """Calendar date of an instant in the policy timezone."""
    return moment.astimezone(_policy_timezone()).date().isoformat()


def _now() -> str:
    """Wall-clock UTC time for record keeping. Never backdated."""
    return datetime.now(timezone.utc).isoformat()


def _environment() -> str:
    # Anything not explicitly marked as development/test is treated as production.
    return os.getenv("PLUM_ENV", "production").strip().casefold() or "production"


def _parse_demo_clock(raw: str) -> tuple[datetime, str]:
    """Return the demo instant (UTC) and the submission date it stands for.

    A bare date is used exactly as given. A datetime without an offset is read
    in the policy timezone; one with an offset is converted to it.
    """
    value = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            given = date.fromisoformat(value)
            return datetime.combine(given, datetime.min.time(), tzinfo=timezone.utc), given.isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("PLUM_DEMO_CLOCK must be an ISO-8601 date (YYYY-MM-DD) or datetime") from exc
    aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=_policy_timezone())
    return aware.astimezone(timezone.utc), _local_date(aware)


def _demo_clock() -> dict[str, Any] | None:
    """Return the explicit development/test submission clock, if one is in effect.

    PLUM_DEMO_CLOCK lets reviewers replay the supplied 2024 policy period. It is
    honored only when PLUM_ENV is development or test; anywhere else it is
    ignored with an error log (and startup refuses to run, see ``lifespan``).
    """
    raw = os.getenv("PLUM_DEMO_CLOCK", "").strip()
    if not raw:
        return None
    environment = _environment()
    if environment not in DEMO_CLOCK_ENVIRONMENTS:
        logger.error(
            "PLUM_DEMO_CLOCK is set but PLUM_ENV=%s; ignoring it and using the real clock. "
            "The demo clock is only honored when PLUM_ENV is development or test.",
            environment,
        )
        return None
    applied_at, submission_date = _parse_demo_clock(raw)
    return {
        "source": "PLUM_DEMO_CLOCK", "value": raw, "applied_at": applied_at.isoformat(),
        "submission_date": submission_date, "environment": environment,
    }


def _check_clock_configuration() -> None:
    """Refuse to start on an unknown policy timezone, or a demo clock outside development."""
    _policy_timezone()
    raw = os.getenv("PLUM_DEMO_CLOCK", "").strip()
    if not raw:
        return
    environment = _environment()
    if environment not in DEMO_CLOCK_ENVIRONMENTS:
        raise RuntimeError(
            f"PLUM_DEMO_CLOCK is set while PLUM_ENV={environment}. Unset PLUM_DEMO_CLOCK, or set "
            "PLUM_ENV=development (or test) for a local demo. Claims are never backdated in production."
        )
    _parse_demo_clock(raw)
    logger.warning(
        "DEMO CLOCK ACTIVE: claims are stamped with submission time %s from PLUM_DEMO_CLOCK (PLUM_ENV=%s). "
        "Every affected claim records this in its decision trace.",
        _parse_demo_clock(raw)[0].isoformat(),
        environment,
    )


def _check_reviewer_configuration() -> None:
    """Production reviewer mutations require a configured shared gateway token."""
    token = os.getenv("PLUM_REVIEW_TOKEN", "")
    if _environment() not in DEMO_CLOCK_ENVIRONMENTS and len(token) < 16:
        raise RuntimeError("PLUM_REVIEW_TOKEN must contain at least 16 characters outside development/test")


def _require_reviewer(request: Request) -> str:
    reviewer_id = request.headers.get("X-Reviewer-ID", "").strip()
    supplied = request.headers.get("X-Reviewer-Token", "")
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            reviewer_id, supplied = decoded.split(":", 1)
            reviewer_id = reviewer_id.strip()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            reviewer_id, supplied = "", ""
    expected = os.getenv("PLUM_REVIEW_TOKEN", "")
    if not reviewer_id or not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Reviewer authentication is required.", headers={"WWW-Authenticate": 'Basic realm="Plum reviewer"'})
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,80}", reviewer_id):
        raise HTTPException(status_code=401, detail="Reviewer identity is invalid.")
    return reviewer_id


def _submission_clock() -> tuple[str, dict[str, Any] | None]:
    """The adjudication-relevant submission time (UTC ISO) and, when overridden, its provenance."""
    clock = _demo_clock()
    if clock is None:
        return _now(), None
    return clock["applied_at"], clock


def _submission_date(submitted_at: str, clock: dict[str, Any] | None) -> str:
    """Submission calendar date: the demo date as given, else the real instant in the policy timezone."""
    if clock is not None:
        return str(clock.get("submission_date") or str(clock["applied_at"])[:10])
    return _local_date(datetime.fromisoformat(submitted_at))


def _clock_trace(clock: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "clock",
        "rule_id": "demo_clock",
        "status": "OVERRIDDEN",
        "evidence": {"source": clock["source"], "value": clock["value"], "submission_date": str(clock.get("submission_date") or str(clock["applied_at"])[:10]), "environment": clock["environment"]},
        "details": "Submission date came from the development/test demo clock, not the real clock. This decision is not a production adjudication.",
    }


def _with_clock_trace(request_data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Make every result for a demo-clock claim visibly record the override."""
    clock = request_data.get("submission_clock")
    if not clock:
        return result
    trace = list(result.get("trace") or [])
    if not any(step.get("stage") == "clock" and step.get("rule_id") == "demo_clock" for step in trace):
        trace.insert(0, _clock_trace(clock))
    result = dict(result)
    result["trace"] = trace
    return result


def _paths() -> tuple[Path, Path]:
    data_root = Path(os.getenv("PLUM_DATA_DIR", PROJECT_ROOT / ".data")).resolve()
    return data_root / "claims.sqlite3", data_root / "uploads"


def _connect() -> sqlite3.Connection:
    db_path, _ = _paths()
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db() -> None:
    """Create local tables. Claim events preserve the sequence behind a result."""
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                state TEXT NOT NULL,
                member_id TEXT,
                treatment_date TEXT,
                decision TEXT,
                approved_amount_paise INTEGER NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL REFERENCES claims(id),
                original_name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                storage_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claim_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL REFERENCES claims(id),
                occurred_at TEXT NOT NULL,
                stage TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS benefit_reservations (
                claim_id TEXT PRIMARY KEY REFERENCES claims(id),
                family_account_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                category TEXT NOT NULL,
                policy_start TEXT NOT NULL,
                policy_end TEXT NOT NULL,
                amount_paise INTEGER NOT NULL CHECK(amount_paise >= 0),
                category_amount_paise INTEGER NOT NULL CHECK(category_amount_paise >= 0),
                status TEXT NOT NULL CHECK(status IN ('RESERVED', 'PAID', 'RELEASED')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settlement_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL REFERENCES claims(id),
                occurred_at TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                reason_text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviewer_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL REFERENCES claims(id),
                occurred_at TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                reason_text TEXT NOT NULL,
                evidence_summary TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_claim_events_claim ON claim_events(claim_id, id);
            CREATE INDEX IF NOT EXISTS idx_benefit_reservations_family ON benefit_reservations(family_account_id, policy_start, policy_end, status);
            CREATE INDEX IF NOT EXISTS idx_benefit_reservations_member_category ON benefit_reservations(member_id, category, policy_start, policy_end, status);
            CREATE INDEX IF NOT EXISTS idx_settlement_events_claim ON settlement_events(claim_id, id);
            CREATE INDEX IF NOT EXISTS idx_reviewer_actions_claim ON reviewer_actions(claim_id, id);
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(claims)")}
        for name, definition in (
            ("member_id", "TEXT"),
            ("treatment_date", "TEXT"),
            ("decision", "TEXT"),
            ("approved_amount_paise", "INTEGER NOT NULL DEFAULT 0"),
            ("bill_fingerprints_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("adjudicated", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE claims ADD COLUMN {name} {definition}")
        # These indexed fields make duplicate checks and member benefit history
        # a bounded lookup instead of reparsing every saved claim on each request.
        connection.execute("CREATE INDEX IF NOT EXISTS idx_claims_member_treatment ON claims(member_id, treatment_date, state)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_documents_hash_claim ON documents(sha256, claim_id)")
        # Earlier versions did not record whether a manual-review result came from the
        # policy engine; the engine always emits a policy_source trace step.
        connection.execute(
            "UPDATE claims SET adjudicated=1 WHERE adjudicated=0 AND state='MANUAL_REVIEW' "
            "AND result_json LIKE '%\"rule_id\": \"policy_source\"%'"
        )
        # Backfill rows created by earlier local app versions.
        for row in connection.execute("SELECT id, request_json, result_json FROM claims WHERE member_id IS NULL OR treatment_date IS NULL OR decision IS NULL"):
            request_data = json.loads(row["request_json"])
            saved_result = json.loads(row["result_json"]) if row["result_json"] else {}
            connection.execute(
                "UPDATE claims SET member_id=COALESCE(member_id, ?), treatment_date=COALESCE(treatment_date, ?), decision=COALESCE(decision, ?), approved_amount_paise=CASE WHEN approved_amount_paise=0 THEN ? ELSE approved_amount_paise END WHERE id=?",
                (
                    request_data.get("member_id"),
                    request_data.get("treatment_date"),
                    saved_result.get("decision"),
                    int(saved_result.get("approved_amount_paise") or 0),
                    row["id"],
                ),
            )


def _record_event(connection: sqlite3.Connection, claim_id: str, stage: str, detail: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO claim_events (claim_id, occurred_at, stage, detail_json) VALUES (?, ?, ?, ?)",
        (claim_id, _now(), stage, json.dumps(detail, default=str)),
    )


def _set_state(
    claim_id: str,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
    adjudicated: bool | None = None,
) -> None:
    """Persist a state change. ``adjudicated`` marks a result produced by the policy engine."""
    with _connect() as connection:
        _set_state_in_connection(
            connection, claim_id, state, result=result, error=error, detail=detail, adjudicated=adjudicated
        )


def _set_state_in_connection(
    connection: sqlite3.Connection,
    claim_id: str,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
    adjudicated: bool | None = None,
) -> None:
    """Persist a state transition on the caller's transaction boundary."""
    if result is not None:
        row = connection.execute("SELECT request_json FROM claims WHERE id=?", (claim_id,)).fetchone()
        if row is not None:
            result = _with_clock_trace(json.loads(row["request_json"]), result)
    connection.execute(
            "UPDATE claims SET state=?, updated_at=?, result_json=?, error_message=?, decision=?, approved_amount_paise=?, "
            "adjudicated=COALESCE(?, adjudicated) WHERE id=?",
            (
                state,
                _now(),
                json.dumps(result, default=str) if result is not None else None,
                error,
                result.get("decision") if result else None,
                int(result.get("approved_amount_paise") or 0) if result else 0,
                None if adjudicated is None else int(adjudicated),
                claim_id,
            ),
    )
    _record_event(connection, claim_id, state, detail or {})


def _load_claim(claim_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            return None
        documents = connection.execute(
            "SELECT id, original_name, media_type, size_bytes, sha256 FROM documents WHERE claim_id = ? ORDER BY rowid",
            (claim_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT occurred_at, stage, detail_json FROM claim_events WHERE claim_id = ? ORDER BY id",
            (claim_id,),
        ).fetchall()
        reservation = connection.execute(
            "SELECT amount_paise, category_amount_paise, status, created_at, updated_at FROM benefit_reservations WHERE claim_id=?",
            (claim_id,),
        ).fetchone()
        reviewer_actions = connection.execute(
            "SELECT occurred_at, reviewer_id, reason_code, reason_text, evidence_summary, before_json, after_json FROM reviewer_actions WHERE claim_id=? ORDER BY id",
            (claim_id,),
        ).fetchall()
        settlement_events = connection.execute(
            "SELECT occurred_at, actor_id, from_status, to_status, reason_code, reason_text FROM settlement_events WHERE claim_id=? ORDER BY id",
            (claim_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "state": row["state"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error_message": row["error_message"],
        "documents": [dict(document) for document in documents],
        "events": [
            {"occurred_at": event["occurred_at"], "stage": event["stage"], "detail": json.loads(event["detail_json"])}
            for event in events
        ],
        "benefit_reservation": dict(reservation) if reservation else None,
        "reviewer_actions": [
            {
                **{key: action[key] for key in ("occurred_at", "reviewer_id", "reason_code", "reason_text", "evidence_summary")},
                "before": json.loads(action["before_json"]), "after": json.loads(action["after_json"]),
            }
            for action in reviewer_actions
        ],
        "settlement_events": [dict(event) for event in settlement_events],
    }


POLICY_PATH = PROJECT_ROOT / "data" / "policy_terms.json"


def _read_policy() -> dict[str, Any]:
    """Validate and normalize the supplied policy; raises PolicyConfigurationError."""
    return load_policy(POLICY_PATH)


def _check_policy_configuration() -> None:
    """Refuse to start on a policy that cannot be adjudicated safely."""
    try:
        _read_policy()
    except PolicyConfigurationError as exc:
        raise RuntimeError(f"Refusing to start: {exc} (policy file {POLICY_PATH})") from exc


def _policy_fingerprint(policy: dict[str, Any]) -> str:
    """The loader's single policy fingerprint; the same value the decision trace reports."""
    return policy_fingerprint(policy)


def _legacy_policy_sha256(policy: dict[str, Any]) -> str:
    """Hash stored by earlier app versions (``request.policy_sha256``); read-only compatibility."""
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()


def _member(policy: dict[str, Any], member_id: str) -> dict[str, Any] | None:
    return next((item for item in policy.get("members", []) if item.get("member_id") == member_id), None)


def _member_name(policy: dict[str, Any], member_id: str) -> str:
    member = _member(policy, member_id)
    return str(member.get("name", "")) if member else ""


def _family_member_ids(policy: dict[str, Any], member_id: str) -> list[str]:
    """The benefit pool a member draws on: the primary member plus their resolved dependents."""
    member = _member(policy, member_id)
    if member is None:
        return []
    owner_id = str(member.get("primary_member_id") or member_id)
    owner = _member(policy, owner_id) or member
    return sorted({owner_id, *[str(value) for value in owner.get("dependents", [])]})


def _covered_member_names(policy: dict[str, Any], member_id: str) -> list[str]:
    covered_ids = set(_family_member_ids(policy, member_id))
    return [str(item.get("name")) for item in policy.get("members", []) if item.get("member_id") in covered_ids and item.get("name")]


def _normalized_name(value: Any) -> str:
    return " ".join(
        word for word in re.findall(r"[a-z]+", str(value or "").casefold())
        if word not in {"mr", "mrs", "ms", "miss", "dr", "shri", "smt"}
    )


def _prefill_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        pass
    for pattern in ("%d-%b-%Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _prefill_suggestions(inspection: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Return document-backed, non-authoritative form suggestions without persistence."""
    documents = inspection.get("documents") or []
    fields = [document.get("content") or {} for document in documents]
    member_by_name = {
        _normalized_name(member.get("name")): member
        for member in policy.get("members", [])
        if _normalized_name(member.get("name"))
    }
    candidate_names = {
        _normalized_name(document.get("patient_name_on_doc") or content.get("patient_name"))
        for document, content in zip(documents, fields, strict=True)
        if _normalized_name(document.get("patient_name_on_doc") or content.get("patient_name"))
    }
    matching_members = {
        str(member_by_name[name].get("member_id")): str(member_by_name[name].get("name"))
        for name in candidate_names if name in member_by_name
    }
    suggestions: dict[str, Any] = {}
    if len(matching_members) == 1:
        member_id, member_name = next(iter(matching_members.items()))
        suggestions["member_id"] = member_id
        suggestions["member_name"] = member_name
    dates = {
        parsed for content in fields
        for value in (content.get("date"), content.get("sample_date"), content.get("report_date"))
        if (parsed := _prefill_date(value))
    }
    if len(dates) == 1:
        suggestions["treatment_date"] = next(iter(dates))
    bill_totals: set[str] = set()
    for document, content in zip(documents, fields, strict=True):
        kind = str(document.get("actual_type") or "").upper()
        if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"} and content.get("total") is not None:
            try:
                total = Decimal(str(content["total"]))
                if total.is_finite() and total > 0:
                    bill_totals.add(f"{total:.2f}")
            except (InvalidOperation, ValueError):
                continue
    if len(bill_totals) == 1:
        suggestions["claimed_amount"] = next(iter(bill_totals))
    category_by_document = {
        "DENTAL_REPORT": "DENTAL",
        "DIAGNOSTIC_REPORT": "DIAGNOSTIC",
        "LAB_REPORT": "DIAGNOSTIC",
        "PHARMACY_BILL": "PHARMACY",
    }
    categories = {
        category_by_document[str(document.get("actual_type") or "").upper()]
        for document in documents
        if str(document.get("actual_type") or "").upper() in category_by_document
    }
    if len(categories) == 1:
        suggestions["claim_category"] = next(iter(categories))
    pre_auth = next(
        (content for document, content in zip(documents, fields, strict=True)
         if str(document.get("actual_type") or "").upper() == "PRE_AUTHORIZATION"),
        None,
    )
    if pre_auth is not None:
        suggestions["pre_authorization_obtained"] = "true"
        if (issued_date := _prefill_date(pre_auth.get("date"))):
            suggestions["pre_authorization_issued_date"] = issued_date
        if (reference := str(pre_auth.get("approval_reference") or "").strip()):
            suggestions["pre_authorization_reference"] = reference
    return {
        "suggestions": suggestions,
        "detected_document_types": sorted({str(document.get("actual_type") or "UNKNOWN") for document in documents}),
        "issues": [
            {key: issue.get(key) for key in ("code", "file_name", "message")}
            for issue in inspection.get("issues") or []
        ],
        "metrics": inspection.get("metrics") or {},
    }


def _gemini_opt_in() -> bool:
    return os.getenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "false").strip().casefold() in {"1", "true", "yes"}


def _gemini_provider_failure(result: dict[str, Any]) -> bool:
    return result.get("status") == "ABSTAINED" and any(
        marker in str((result.get("trace") or {}).get("reason") or "")
        for marker in ("timeout", "connection_error", "provider_error", "rate_limited", "provider_unavailable", "provider_not_configured", "provider_dependency_unavailable", "resolver_failure", "candidate_application_failed")
    )


def _safe_name(name: str | None) -> str:
    raw = Path((name or "document").replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9._ -]", "_", raw)[:120] or "document"


def _input_error(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


async def _read_upload(upload: UploadFile) -> tuple[str, str, bytes]:
    data = await upload.read(MAX_FILE_BYTES + 1)
    if not data:
        raise _input_error(f"{_safe_name(upload.filename)} is empty. Upload a PDF or clear image.")
    if len(data) > MAX_FILE_BYTES:
        raise _input_error(f"{_safe_name(upload.filename)} exceeds the 10 MB file limit.")
    actual_type = sniff_media_type(data)
    if actual_type not in ALLOWED_TYPES:
        raise _input_error(f"{_safe_name(upload.filename)} is not a supported PDF, JPEG, PNG, or WebP file.")
    return _safe_name(upload.filename), actual_type, data


def _correction_result(
    issues: list[dict[str, Any]], metrics: dict[str, Any], extra_trace: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    correction_requests = [
        {
            "code": str(issue.get("code") or "DOCUMENT_ISSUE"),
            "message": str(issue.get("message") or "Upload a clearer or correct document."),
            "file_name": issue.get("file_name"),
            "required_type": issue.get("required_type"),
        }
        for issue in issues
    ]
    return {
        "state": "DOCUMENT_CORRECTION_REQUIRED",
        "decision": None,
        "approved_amount": None,
        "approved_amount_paise": None,
        "reasons": list(correction_requests),
        "correction_requests": correction_requests,
        "confidence_score": None,
        "ledger": [],
        "trace": [
            {
                "stage": "document_gate",
                "status": "BLOCKED",
                "rule_id": str(issue.get("code", "DOCUMENT_ISSUE")),
                "evidence": {"file_name": issue.get("file_name"), "required_type": issue.get("required_type")},
                "reason": issue.get("message"),
            }
            for issue in issues
        ] + (extra_trace or []),
        "metrics": metrics,
    }


def _provider_review_result(issues: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    """Keep provider outages separate from member document corrections."""
    return {
        "state": "MANUAL_REVIEW",
        "decision": "MANUAL_REVIEW",
        "approved_amount": 0,
        "approved_amount_paise": 0,
        "reasons": [
            {
                "code": "EXTRACTION_UNAVAILABLE",
                "message": "Document extraction is unavailable. An operator must inspect the uploaded files.",
            }
        ],
        "correction_requests": [],
        "confidence_score": 0.2,
        "ledger": [],
        "trace": [
            {
                "stage": "document_extraction",
                "status": "DEGRADED",
                "rule_id": str(issue.get("code", "EXTRACTION_UNAVAILABLE")),
                "evidence": {"file_name": issue.get("file_name")},
                "reason": issue.get("message"),
            }
            for issue in issues
        ],
        "document_metrics": metrics,
    }


def _document_risk_review_result(issues: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    """Route possible alteration or duplicate stamps to an operator, never to automatic fraud rejection."""
    signals = [issue for issue in issues if issue.get("code") in {"DOCUMENT_ALTERATION", "DUPLICATE_STAMP"}]
    return {
        "state": "MANUAL_REVIEW",
        "decision": "MANUAL_REVIEW",
        "approved_amount": 0,
        "approved_amount_paise": 0,
        "reasons": [
            {
                "code": str(issue["code"]),
                "message": str(issue["message"]),
            }
            for issue in signals
        ],
        "correction_requests": [],
        "confidence_score": 0.2,
        "ledger": [],
        "trace": [
            {
                "stage": "document_risk",
                "rule_id": str(issue["code"]).lower(),
                "status": "FLAG",
                "evidence": {"file_name": issue.get("file_name"), "signals": issue.get("signals", {})},
                "details": "A document signal requests human inspection; it does not assert fraud.",
            }
            for issue in signals
        ],
        "document_metrics": metrics,
    }


# A claim counts toward the member's claim history (same-day and monthly
# frequency limits) only once it has been submitted as a valid claim: either the
# policy engine adjudicated it (DECIDED, or MANUAL_REVIEW with adjudicated=1), or a
# reviewer recorded a final decision. Uploads stopped at the document gate
# (DOCUMENT_CORRECTION_REQUIRED), provider outages and duplicate-bill holds awaiting
# review, queued/processing jobs, and failed jobs never count: they are attempts to
# submit, not claims. Rejected claims do count; they were real submissions.
COUNTED_CLAIM_SQL = "(state='DECIDED' OR (state='MANUAL_REVIEW' AND adjudicated=1))"
# Legacy payable rows remain readable for databases created before the separate
# reservation ledger. New decisions consume benefit through RESERVED/PAID rows;
# RELEASED rows return it to availability.
PAYABLE_CLAIM_SQL = "(state='DECIDED' AND decision IN ('APPROVED', 'PARTIAL') AND approved_amount_paise>0)"


def _member_claim_history(
    claim_id: str,
    request_data: dict[str, Any],
    policy: dict[str, Any],
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Family claim frequency, benefit usage, and session history for the policy year."""
    member_id = str(request_data["member_id"])
    category = str(request_data.get("claim_category") or "")
    family_ids = _family_member_ids(policy, member_id)
    usage: dict[str, Any] = {
        "claims_history": [], "family_approved_paise": 0, "member_category_approved_paise": 0, "prior_sessions": 0,
        # Exact per-member usage of the category sub-limit, summed from earlier decisions'
        # own trace. None when any earlier payable decision predates that trace step.
        "member_category_sub_limit_paise": 0,
    }
    if not family_ids:
        return usage
    start = str(policy.get("policy_holder", {}).get("policy_start_date", "0001-01-01"))
    end = str(policy.get("policy_holder", {}).get("policy_end_date", "9999-12-31"))
    marks = ",".join("?" for _ in family_ids)
    owns_connection = connection is None
    active = connection or _connect()
    try:
        rows = active.execute(
            f"""SELECT claims.id, claims.member_id, claims.treatment_date, claims.request_json, claims.result_json, claims.approved_amount_paise,
                       {PAYABLE_CLAIM_SQL} AS payable,
                       benefit_reservations.amount_paise AS reserved_amount_paise,
                       benefit_reservations.category_amount_paise AS reserved_category_paise,
                       benefit_reservations.status AS reservation_status
                FROM claims LEFT JOIN benefit_reservations ON benefit_reservations.claim_id=claims.id
                WHERE claims.id<>? AND claims.member_id IN ({marks}) AND claims.treatment_date BETWEEN ? AND ?
                  AND {COUNTED_CLAIM_SQL}
                ORDER BY claims.created_at""",
            (claim_id, *family_ids, start, end),
        ).fetchall()
    finally:
        if owns_connection:
            active.close()
    for row in rows:
        try:
            prior_request = json.loads(row["request_json"])
            prior_result = json.loads(row["result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            prior_request, prior_result = {}, {}
        # Amount and provider feed the risk-signal enrichment (accumulated value, repeat billing).
        usage["claims_history"].append({
            "claim_id": row["id"], "date": row["treatment_date"],
            "amount": prior_request.get("claimed_amount"), "provider": prior_result.get("provider") or None,
        })
        active_reservation = row["reservation_status"] in {"RESERVED", "PAID"}
        legacy_payable = bool(row["payable"]) and row["reservation_status"] is None
        if not active_reservation and not legacy_payable:
            continue
        approved = int(row["reserved_amount_paise"] if active_reservation else row["approved_amount_paise"] or 0)
        usage["family_approved_paise"] += approved
        if row["member_id"] != member_id or prior_request.get("claim_category") != category:
            continue
        usage["member_category_approved_paise"] += approved
        sub_limit_step = next((step for step in prior_result.get("trace", []) if step.get("rule_id") == "category_sub_limit"), None)
        counted = row["reserved_category_paise"] if active_reservation else (sub_limit_step or {}).get("evidence", {}).get("counted_against_sub_limit_paise")
        if usage["member_category_sub_limit_paise"] is not None:
            usage["member_category_sub_limit_paise"] = None if counted is None else usage["member_category_sub_limit_paise"] + int(counted)
        if category == "ALTERNATIVE_MEDICINE":
            for step in prior_result.get("trace", []):
                if step.get("rule_id") == "max_sessions":
                    usage["prior_sessions"] += int((step.get("evidence") or {}).get("current_sessions") or 0)
                    break
    return usage


def _normal_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _bill_fingerprints(inspected_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document-derived keys that identify the same bill across re-renders and resubmissions.

    Only facts printed on the bill are used, never the member-typed treatment date,
    so changing the claim form cannot make a paid bill look new. A numbered bill is
    keyed by bill number, provider, and total (plus its printed date and patient when
    readable); an unnumbered bill needs provider, total, printed date, and patient name.
    """
    fingerprints: list[dict[str, Any]] = []
    for document in inspected_documents:
        if str(document.get("actual_type") or document.get("doc_type") or "").upper() not in BILL_TYPES:
            continue
        fields = document.get("content") or document.get("fields") or {}
        provider = _normal_token(fields.get("hospital_name"))
        try:
            total_paise = to_paise(fields.get("total"))
        except ValueError:
            continue
        if not provider or total_paise <= 0:
            continue
        printed_date = parse_document_date(fields.get("date"))
        document_date = printed_date.isoformat() if printed_date else None
        bill_number = _normal_token(fields.get("bill_number"))
        patient = normal_name(fields.get("patient_name") or document.get("patient_name_on_doc"))
        if bill_number:
            fingerprints.append({
                "kind": "bill_number", "bill_number": bill_number, "provider": provider,
                "total_paise": total_paise, "document_date": document_date, "patient": patient or None,
            })
        elif document_date and patient:
            fingerprints.append({
                "kind": "unnumbered_bill", "provider": provider, "total_paise": total_paise,
                "document_date": document_date, "patient": patient,
            })
    return fingerprints


# The shorter provider token must be at least this long to count as the same provider
# as a longer one, so a stray initial or a one-word name cannot match everything.
MIN_PROVIDER_TOKEN = 5


def _same_provider(first: Any, second: Any) -> bool:
    """Whether two normalised provider tokens name the same provider.

    Tokens are the provider name with everything but letters and digits removed
    (the stored shape, including rows from earlier versions). A branch or location
    suffix or prefix ("Apollo Hospitals, Indiranagar") does not make a new provider:
    the shorter token only has to appear inside the longer one.
    """
    a, b = _normal_token(first), _normal_token(second)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= MIN_PROVIDER_TOKEN and shorter in longer


def _fingerprints_match(new: dict[str, Any], saved: dict[str, Any]) -> bool:
    """Whether two bill fingerprints plausibly describe the same bill.

    Matching holds a claim for review; it never rejects one, so it errs towards a match.
    """
    saved_kind = str(saved.get("kind") or "bill_number")  # rows from earlier versions carry no kind
    new_kind = str(new.get("kind") or "bill_number")
    if new.get("total_paise") != saved.get("total_paise"):
        return False
    new_date, saved_date = new.get("document_date"), saved.get("document_date")
    same_provider = _same_provider(new.get("provider"), saved.get("provider"))
    if saved_kind == new_kind == "bill_number":
        if new.get("bill_number") != saved.get("bill_number"):
            return False
        if new_date and saved_date:
            # Two readable, different dates are two bills. The same number, total, and
            # printed date is the same bill even when the provider name is written
            # differently (a branch suffix, a renamed letterhead): it is held for review.
            return bool(new_date == saved_date)
        # A missing printed date cannot distinguish two bills; the provider must agree.
        return same_provider
    # Unnumbered on either side (the bill number may have been dropped on a re-render):
    # provider, total, printed date, and patient must all agree.
    new_patient, saved_patient = new.get("patient"), saved.get("patient")
    return bool(
        same_provider and new_date and new_date == saved_date
        and new_patient and new_patient == saved_patient
    )


def _save_bill_fingerprints(claim_id: str, fingerprints: list[dict[str, Any]]) -> None:
    with _connect() as connection:
        connection.execute("UPDATE claims SET bill_fingerprints_json=? WHERE id=?", (json.dumps(fingerprints), claim_id))


# Claims a new bill is compared against: paid claims, plus claims still in flight
# or awaiting review (so two concurrent or back-to-back submissions of one bill
# cannot both be paid). Rejected, failed, and correction-required claims are excluded.
DUPLICATE_CANDIDATE_SQL = f"({PAYABLE_CLAIM_SQL} OR state IN ('PROCESSING', 'MANUAL_REVIEW'))"


def _duplicate_bill_hits(claim_id: str, inspected_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find paid or pending claims with the same bill file or the same logical bill."""
    bill_hashes = {
        str(document.get("sha256"))
        for document in inspected_documents
        if str(document.get("actual_type") or document.get("doc_type") or "").upper() in BILL_TYPES
        and document.get("sha256")
    }
    hits: list[dict[str, Any]] = []
    with _connect() as connection:
        for digest in sorted(bill_hashes):
            rows = connection.execute(
                f"""SELECT DISTINCT claims.id, claims.state
                   FROM documents JOIN claims ON claims.id=documents.claim_id
                   WHERE documents.sha256=? AND documents.claim_id<>? AND {DUPLICATE_CANDIDATE_SQL}
                   ORDER BY claims.id""",
                (digest, claim_id),
            ).fetchall()
            if rows:
                hits.append({
                    "match_type": "identical_bill_file",
                    "previous_claim_ids": [row["id"] for row in rows],
                    "previous_states": {row["id"]: row["state"] for row in rows},
                })
        fingerprints = _bill_fingerprints(inspected_documents)
        if fingerprints:
            rows = connection.execute(
                f"""SELECT id, state, bill_fingerprints_json FROM claims
                   WHERE id<>? AND bill_fingerprints_json<>'[]' AND {DUPLICATE_CANDIDATE_SQL}
                   ORDER BY id""",
                (claim_id,),
            ).fetchall()
            matched = []
            for row in rows:
                saved = json.loads(row["bill_fingerprints_json"] or "[]")
                if any(_fingerprints_match(new, old) for new in fingerprints for old in saved):
                    matched.append(row)
            if matched:
                hits.append({
                    "match_type": "logical_bill_fingerprint",
                    "previous_claim_ids": [row["id"] for row in matched],
                    "previous_states": {row["id"]: row["state"] for row in matched},
                })
    return hits


def _duplicate_review_result(hits: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    prior_ids = sorted({claim_id for hit in hits for claim_id in hit["previous_claim_ids"]})
    states = {claim_id: state for hit in hits for claim_id, state in hit.get("previous_states", {}).items()}
    return {
        "state": "MANUAL_REVIEW",
        "decision": "MANUAL_REVIEW",
        "approved_amount": 0,
        "approved_amount_paise": 0,
        "reasons": [{"code": "DUPLICATE_BILL", "message": "Another paid or pending claim has an identical bill file, or a bill with the same bill number, amount, and printed date (or, without a readable date or bill number, the same provider, amount, and patient). Verify that the expense has not already been reimbursed."}],
        "correction_requests": [],
        "confidence_score": 0.2,
        "ledger": [],
        "trace": [{
            "stage": "duplicate_check", "rule_id": "previous_bill_match", "status": "FLAG",
            "evidence": {
                "matching_claim_count": len(prior_ids), "matching_claim_ids": prior_ids,
                "matching_claim_states": states, "match_types": sorted({hit["match_type"] for hit in hits}),
            },
            "details": "Bill files and document-derived bill fingerprints (never the claim-form treatment date) are checked against paid, in-flight, and under-review claims.",
        }],
        "document_metrics": metrics,
    }


def _demo_clock_quarantine_result(clock: dict[str, Any], environment: str) -> dict[str, Any]:
    """A claim stamped by the development demo clock must not be adjudicated outside development."""
    return {
        "state": "MANUAL_REVIEW",
        "decision": "MANUAL_REVIEW",
        "approved_amount": 0,
        "approved_amount_paise": 0,
        "reasons": [{"code": "DEMO_CLOCK_NOT_HONORED", "message": "This claim was stamped with a development demo-clock submission date and is being processed outside development. It was not adjudicated; a reviewer must confirm the real submission date."}],
        "correction_requests": [],
        "confidence_score": 0.0,
        "ledger": [],
        "trace": [{
            "stage": "clock", "rule_id": "demo_clock_not_honored", "status": "FAIL",
            "evidence": {
                "stamped_submission_date": clock.get("submission_date") or str(clock.get("applied_at", ""))[:10],
                "stamped_environment": clock.get("environment"), "processing_environment": environment,
            },
            "details": "Demo-clock dates are honored only when PLUM_ENV is development or test. The claim was routed to manual review instead of being adjudicated with the demo date.",
        }],
    }


def _category_reserved_paise(result: dict[str, Any]) -> int:
    step = next((item for item in result.get("trace", []) if item.get("rule_id") == "category_sub_limit"), None)
    value = (step or {}).get("evidence", {}).get("counted_against_sub_limit_paise")
    return max(0, int(value or 0))


def _adjudicate_with_reservation(
    claim_id: str,
    request_data: dict[str, Any],
    policy: dict[str, Any],
    documents: list[dict[str, Any]],
    metrics: dict[str, Any],
    gemini_trace: list[dict[str, Any]],
) -> None:
    """Serialize balance reads, decision, reservation, and persistence in one transaction.

    ``BEGIN IMMEDIATE`` is the SQLite equivalent of taking the benefit-account
    write lock. A second worker cannot read the same available balance and reserve
    it until this transaction commits.
    """
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT state FROM claims WHERE id=?", (claim_id,)).fetchone()
        if current is None or current["state"] != "PROCESSING":
            return
        payload = dict(request_data)
        payload["documents"] = documents
        usage = _member_claim_history(claim_id, request_data, policy, connection)
        family_used = to_rupees(usage["family_approved_paise"])
        payload["claims_history"] = usage["claims_history"]
        payload["claims_history_source"] = "atomic_benefit_reservation_ledger"
        payload["ytd_claims_amount"] = family_used
        payload["ytd_claims_source"] = "reserved_or_paid_benefit_ledger"
        payload["sum_insured_used"] = family_used
        payload["sum_insured_used_source"] = "reserved_or_paid_benefit_ledger"
        payload["family_floater_used"] = family_used
        payload["family_floater_used_source"] = "reserved_or_paid_benefit_ledger"
        payload["category_ytd_claims_amount"] = to_rupees(usage["member_category_approved_paise"])
        payload["category_ytd_claims_source"] = "reserved_or_paid_benefit_ledger"
        if usage["member_category_sub_limit_paise"] is not None:
            payload["category_sub_limit_used"] = to_rupees(usage["member_category_sub_limit_paise"])
            payload["category_sub_limit_used_source"] = "atomic_benefit_reservation_ledger"
        payload["prior_sessions"] = usage["prior_sessions"]
        payload["prior_sessions_source"] = "reserved_or_paid_benefit_ledger"
        result = adjudicate_handoff(payload, policy, evaluate_claim)
        result.setdefault("provider", claim_provider(payload) or None)
        result.setdefault("document_metrics", metrics)
        result["trace"] = [document_evidence_trace(documents)] + gemini_trace + result.get("trace", [])
        approved = max(0, int(result.get("approved_amount_paise") or 0))
        decision = str(result.get("decision") or "")
        if decision in {"APPROVED", "PARTIAL"} and approved:
            member = _member(policy, str(request_data["member_id"])) or {}
            family_account_id = str(member.get("primary_member_id") or request_data["member_id"])
            policy_holder = policy.get("policy_holder", {})
            now = _now()
            category_reserved = min(approved, _category_reserved_paise(result))
            connection.execute(
                """INSERT INTO benefit_reservations
                   (claim_id, family_account_id, member_id, category, policy_start, policy_end,
                    amount_paise, category_amount_paise, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)""",
                (
                    claim_id, family_account_id, request_data["member_id"], request_data["claim_category"],
                    policy_holder.get("policy_start_date", "0001-01-01"),
                    policy_holder.get("policy_end_date", "9999-12-31"), approved, category_reserved, now, now,
                ),
            )
            result.setdefault("trace", []).append({
                "stage": "benefit_ledger", "rule_id": "atomic_benefit_reservation", "status": "RESERVED",
                "evidence": {
                    "family_account_id": family_account_id, "amount_paise": approved,
                    "category_amount_paise": category_reserved, "settlement_status": "RESERVED",
                },
                "details": "Benefit availability was read and reserved under the same database write transaction.",
            })
        final_state = str(result.get("state") or result.get("decision") or "MANUAL_REVIEW")
        _set_state_in_connection(
            connection, claim_id, final_state, result=result,
            detail={"decision": result.get("decision"), "benefit_reservation": "RESERVED" if approved and decision in {"APPROVED", "PARTIAL"} else "NONE"},
            adjudicated=True,
        )


def process_claim(claim_id: str) -> None:
    """Run one persisted claim; safe to retry after process restart."""
    claim = _load_claim(claim_id)
    if claim is None or claim["state"] not in {"QUEUED", "PROCESSING"}:
        return
    _set_state(claim_id, "PROCESSING", detail={"message": "Checking submitted documents"})
    try:
        request_data = claim["request"]
        environment = _environment()
        stamped_clock = request_data.get("submission_clock")
        if stamped_clock and environment not in DEMO_CLOCK_ENVIRONMENTS:
            # A demo-clock claim recovered or retried outside development keeps its
            # recorded provenance but is never adjudicated with the demo date.
            logger.error("Claim %s carries a demo-clock submission date but PLUM_ENV=%s; routing to manual review.", claim_id, environment)
            _set_state(
                claim_id, "MANUAL_REVIEW", result=_demo_clock_quarantine_result(stamped_clock, environment),
                detail={"reason": "demo_clock_outside_development", "processing_environment": environment},
            )
            return
        policy = _read_policy()
        current_fingerprint = _policy_fingerprint(policy)
        if request_data.get("policy_canonical_sha256"):
            submitted_fingerprint = str(request_data["policy_canonical_sha256"])
            policy_changed = submitted_fingerprint != current_fingerprint
        elif request_data.get("policy_sha256"):
            submitted_fingerprint = str(request_data["policy_sha256"])
            policy_changed = submitted_fingerprint != _legacy_policy_sha256(policy)
        else:
            submitted_fingerprint, policy_changed = "", False
        if policy_changed:
            snapshot_result: dict[str, Any] = {
                "state": "MANUAL_REVIEW",
                "decision": "MANUAL_REVIEW",
                "approved_amount": 0,
                "approved_amount_paise": 0,
                "reasons": [{"code": "POLICY_CHANGED", "message": "The policy changed after this claim was submitted. A reviewer must re-evaluate it against the current policy."}],
                "correction_requests": [],
                "confidence_score": 0.0,
                "ledger": [],
                "trace": [{"stage": "policy", "rule_id": "policy_snapshot", "status": "FAIL", "policy_ref": "claim.policy_canonical_sha256", "evidence": {"submitted_fingerprint": submitted_fingerprint, "current_canonical_sha256": current_fingerprint}, "details": "The policy fingerprint captured at intake does not match the current policy."}],
            }
            _set_state(claim_id, "MANUAL_REVIEW", result=snapshot_result, detail={"reason": "policy_changed"})
            return
        _, upload_root = _paths()
        with _connect() as connection:
            file_rows = connection.execute(
                "SELECT original_name, media_type, storage_path FROM documents WHERE claim_id = ? ORDER BY rowid",
                (claim_id,),
            ).fetchall()
        files = [
            {
                "file_name": row["original_name"],
                "content_type": row["media_type"],
                "data": (upload_root / row["storage_path"]).read_bytes(),
            }
            for row in file_rows
        ]
        inspection = process_uploads(
            files,
            request_data["claim_category"],
            _member_name(policy, request_data["member_id"]),
            policy,
            allowed_patient_names=_covered_member_names(policy, request_data["member_id"]),
        )
        # OCR page text is available only during this worker call. It is removed
        # from the inspection before metrics, events, or results are persisted.
        ocr_text_by_file_id = inspection.pop("ocr_text_by_file_id", {})
        issues = inspection.get("issues", [])

        ai_result: dict[str, Any] | None = None
        gemini_trace: list[dict[str, Any]] = []
        if _gemini_opt_in():
            files_by_id = {
                f"UPLOAD-{index}": {
                    "data": item["data"],
                    "mime_type": item.get("content_type"),
                }
                for index, item in enumerate(files, 1)
            }
            handoff = resolve_document_handoff(
                inspection,
                files_by_id,
                ocr_text_by_file_id,
                request_data["claim_category"],
                _member_name(policy, request_data["member_id"]),
                _covered_member_names(policy, request_data["member_id"]),
                policy,
                resolve_evidence,
            )
            inspection["documents"] = handoff["documents"]
            inspection["issues"] = issues = handoff["issues"]
            inspection["metrics"] = handoff["metrics"]
            gemini_trace = handoff["trace"]
            ai_result = {"status": handoff["status"], "trace": gemini_trace[0]}
        else:
            inspection.setdefault("metrics", {})["gemini"] = {"status": "DISABLED", "calls": 0, "pages": 0}

        # Duplicate detection runs before the correction gate: a resubmitted bill that
        # was already paid must reach a reviewer, not be sent back for a fresh date.
        documents = inspection.get("documents", [])
        _save_bill_fingerprints(claim_id, _bill_fingerprints(documents))
        duplicate_hits = _duplicate_bill_hits(claim_id, documents)
        if duplicate_hits:
            result = _duplicate_review_result(duplicate_hits, inspection.get("metrics", {}))
            result["trace"].extend(gemini_trace)
            _set_state(
                claim_id,
                "MANUAL_REVIEW",
                result=result,
                detail={"duplicate_bill_match_count": len({cid for hit in duplicate_hits for cid in hit["previous_claim_ids"]})},
            )
            return
        if ai_result and _gemini_provider_failure(ai_result) and issues:
            result = _provider_review_result(issues, inspection.get("metrics", {}))
            result["trace"].extend(gemini_trace)
            _set_state(
                claim_id,
                "MANUAL_REVIEW",
                result=result,
                detail={"issue_count": len(issues), "gemini_status": ai_result.get("status")},
            )
            return
        if any(issue.get("code") in {"DOCUMENT_ALTERATION", "DUPLICATE_STAMP"} for issue in issues):
            result = _document_risk_review_result(issues, inspection.get("metrics", {}))
            result["trace"].extend(gemini_trace)
            _set_state(claim_id, "MANUAL_REVIEW", result=result, detail={"document_risk_signal_count": len(result["reasons"])})
            return
        if issues:
            metrics = inspection.get("metrics", {})
            if any(issue.get("code") == "EXTRACTION_UNAVAILABLE" for issue in issues):
                result = _provider_review_result(issues, metrics)
                result["trace"].extend(gemini_trace)
                _set_state(
                    claim_id,
                    "MANUAL_REVIEW",
                    result=result,
                    detail={"issue_count": len(issues), "provider_failures": metrics.get("provider_failures", 0)},
                )
            else:
                result = _correction_result(issues, metrics, gemini_trace)
                _set_state(claim_id, "DOCUMENT_CORRECTION_REQUIRED", result=result, detail={"issue_count": len(issues)})
            return
        _adjudicate_with_reservation(
            claim_id, request_data, policy, documents, inspection.get("metrics", {}), gemini_trace
        )
    except Exception as exc:  # noqa: BLE001 - isolate all provider and parser failures at the job boundary
        # Provider exceptions can contain document text, so retain the type only.
        _set_state(
            claim_id,
            "PROCESSING_FAILED",
            error=f"Processing could not complete ({type(exc).__name__}). Please retry.",
            detail={"error_type": type(exc).__name__},
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _check_clock_configuration()
    _check_reviewer_configuration()
    _check_policy_configuration()
    init_db()
    with _connect() as connection:
        pending = [row["id"] for row in connection.execute("SELECT id FROM claims WHERE state IN ('QUEUED', 'PROCESSING')")]
    recovery = [asyncio.create_task(asyncio.to_thread(process_claim, claim_id)) for claim_id in pending]
    yield
    for task in recovery:
        if not task.done():
            task.cancel()


app = FastAPI(title="Plum OPD Claims Review", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static", check_dir=False), name="static")
templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")


@app.exception_handler(PolicyConfigurationError)
async def _policy_configuration_error(_: Request, exc: PolicyConfigurationError) -> JSONResponse:
    # The policy was valid at startup but has since become unusable: fail closed, never 500.
    logger.error("Policy configuration invalid: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": exc.code, "message": "The policy configuration is invalid; claims cannot be accepted until it is fixed."}},
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    policy = _read_policy()
    members = [{"id": member["member_id"], "name": member["name"]} for member in policy.get("members", [])]
    return templates.TemplateResponse(
        request, "index.html", {"members": members, "categories": CATEGORIES, "demo_clock": _demo_clock()}
    )


@app.get("/claims/{claim_id}", response_class=HTMLResponse)
def claim_page(request: Request, claim_id: str) -> HTMLResponse:
    claim = _load_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return templates.TemplateResponse(request, "claim.html", {"claim_id": claim_id})


@app.get("/ops", response_class=HTMLResponse)
def operations_page(request: Request) -> HTMLResponse:
    """Authenticated reviewer worklist."""
    _require_reviewer(request)
    return templates.TemplateResponse(request, "ops.html")


@app.post("/api/claims", status_code=202)
async def submit_claim(
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
    member_id: str = Form(...),
    claim_category: str = Form(...),
    treatment_date: str = Form(...),
    claimed_amount: str = Form(...),
    pre_authorization_obtained: str | None = Form(None),
    pre_authorization_issued_date: str | None = Form(None),
    pre_authorization_reference: str | None = Form(None),
) -> JSONResponse:
    member_id = member_id.strip().upper()
    claim_category = claim_category.strip().upper()
    if claim_category not in CATEGORIES:
        raise _input_error("Choose a supported claim category.")
    policy = _read_policy()
    if not _member_name(policy, member_id):
        raise _input_error("Choose a member listed in this policy.")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", treatment_date):
            raise ValueError("invalid format")
        date.fromisoformat(treatment_date)
    except ValueError as exc:
        raise _input_error("Enter a treatment date in YYYY-MM-DD format.") from exc
    try:
        if has_subpaise_precision(claimed_amount):
            raise ValueError("more than two decimal places")
        amount_paise = to_paise(claimed_amount)
    except ValueError as exc:
        raise _input_error("Enter a valid claimed amount.") from exc
    if amount_paise <= 0 or amount_paise > 100_000_000:
        raise _input_error("Enter a claimed amount greater than zero and below ₹10 lakh.")
    if not 1 <= len(files) <= MAX_FILES:
        raise _input_error("Upload between one and six documents.")
    checked = [await _read_upload(file) for file in files]
    if sum(len(data) for _, _, data in checked) > MAX_TOTAL_BYTES:
        raise _input_error("The combined upload exceeds the 30 MB limit.")

    claim_id = uuid.uuid4().hex
    now = _now()
    submitted_at, clock = _submission_clock()
    _, upload_root = _paths()
    claim_root = upload_root / claim_id
    claim_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    stored: list[tuple[str, str, str, int, str, str]] = []
    for name, media_type, data in checked:
        file_id = uuid.uuid4().hex
        path = claim_root / file_id
        path.write_bytes(data)
        path.chmod(0o600)
        stored.append((file_id, name, media_type, len(data), hashlib.sha256(data).hexdigest(), f"{claim_id}/{file_id}"))
    request_data = {
        "member_id": member_id,
        "policy_id": policy.get("policy_id"),
        "policy_canonical_sha256": _policy_fingerprint(policy),
        "claim_category": claim_category,
        "treatment_date": treatment_date,
        "claimed_amount": to_rupees(amount_paise),
        "submitted_at": submitted_at,
        "submission_date": _submission_date(submitted_at, clock),
        "submission_timezone": str(_policy_timezone()),
    }
    if clock is not None:
        # Provenance travels with the claim so the trace and UI can show the override.
        request_data["submission_clock"] = clock
    if pre_authorization_obtained in {"true", "false"}:
        request_data["pre_authorization"] = {
            "obtained": pre_authorization_obtained == "true",
            "issued_date": (pre_authorization_issued_date or "").strip(),
            "approval_reference": (pre_authorization_reference or "").strip(),
        }
    with _connect() as connection:
        connection.execute(
            "INSERT INTO claims (id, created_at, updated_at, state, member_id, treatment_date, request_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (claim_id, now, now, "QUEUED", member_id, treatment_date, json.dumps(request_data)),
        )
        connection.executemany(
            "INSERT INTO documents (id, claim_id, original_name, media_type, size_bytes, sha256, storage_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(file_id, claim_id, name, media_type, size, digest, path) for file_id, name, media_type, size, digest, path in stored],
        )
        _record_event(connection, claim_id, "QUEUED", {"file_count": len(stored)})
        if clock is not None:
            _record_event(connection, claim_id, "DEMO_CLOCK", {"source": clock["source"], "value": clock["value"], "submission_date": request_data["submission_date"]})
    background_tasks.add_task(process_claim, claim_id)
    return JSONResponse({"id": claim_id, "state": "QUEUED", "url": f"/claims/{claim_id}"}, status_code=202)


@app.post("/api/claims/prefill")
async def prefill_claim(files: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
    """Inspect uploads for reviewable intake suggestions without saving a claim or files."""
    if not 1 <= len(files) <= MAX_FILES:
        raise _input_error("Upload between one and six documents.")
    checked = [await _read_upload(file) for file in files]
    if sum(len(data) for _, _, data in checked) > MAX_TOTAL_BYTES:
        raise _input_error("The combined upload exceeds the 30 MB limit.")
    policy = _read_policy()
    inspection = process_uploads(
        [
            {"file_name": name, "content_type": media_type, "data": data}
            for name, media_type, data in checked
        ],
        "CONSULTATION",
        "",
        policy,
        allowed_patient_names=[str(member.get("name")) for member in policy.get("members", [])],
    )
    inspection.pop("ocr_text_by_file_id", None)
    return _prefill_suggestions(inspection, policy)


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: str) -> dict[str, Any]:
    claim = _load_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@app.post("/api/claims/{claim_id}/review-decision")
def resolve_manual_review(request: Request, claim_id: str, disposition: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Record an authenticated, attributable reviewer disposition and benefit reservation."""
    reviewer_id = _require_reviewer(request)
    decision = str(disposition.get("decision") or "").upper()
    if decision not in {"APPROVED", "PARTIAL", "REJECTED"}:
        raise _input_error("Reviewer decision must be APPROVED, PARTIAL, or REJECTED.")
    try:
        raw_amount = disposition.get("approved_amount", 0)
        if has_subpaise_precision(raw_amount):
            raise ValueError("more than two decimal places")
        amount_paise = to_paise(raw_amount, allow_negative=True)
    except ValueError as exc:
        raise _input_error("Reviewer approved amount must be a valid amount.") from exc
    reason_code = str(disposition.get("reason_code") or "").strip().upper()
    reason_text = str(disposition.get("reason_text") or "").strip()
    evidence_summary = str(disposition.get("evidence_summary") or "").strip()
    if not re.fullmatch(r"[A-Z0-9_]{3,60}", reason_code) or not reason_text or not evidence_summary:
        raise _input_error("Reviewer reason_code, reason_text, and evidence_summary are required.")
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT state, request_json, result_json FROM claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Claim not found")
        if row["state"] != "MANUAL_REVIEW":
            raise HTTPException(status_code=409, detail="Only a manual-review claim can receive a reviewer disposition.")
        request_data = json.loads(row["request_json"])
        before = json.loads(row["result_json"] or "{}")
        claimed_paise = to_paise(request_data["claimed_amount"])
        if amount_paise < 0 or amount_paise > claimed_paise or (decision == "REJECTED" and amount_paise != 0) or (decision != "REJECTED" and amount_paise <= 0):
            raise _input_error("Reviewer amount is inconsistent with the requested decision or claim amount.")
        policy = _read_policy()
        if decision in {"APPROVED", "PARTIAL"}:
            usage = _member_claim_history(claim_id, request_data, policy, connection)
            limits = policy["limits"]
            available = min(
                max(0, limits["annual_opd_limit_paise"] - usage["family_approved_paise"]),
                max(0, limits["sum_insured_per_employee_paise"] - usage["family_approved_paise"]),
                max(0, limits["family_floater"]["combined_limit_paise"] - usage["family_approved_paise"]),
            )
            category_limit = policy["categories"][request_data["claim_category"]]["sub_limit_paise"]
            category_used = int(usage["member_category_sub_limit_paise"] or 0)
            category_available = max(0, category_limit - category_used)
            available = min(available, category_available)
            if amount_paise > available:
                raise HTTPException(status_code=409, detail=f"Only {to_rupees(available)} remains available for this member and category.")
            member = _member(policy, request_data["member_id"]) or {}
            family_account_id = str(member.get("primary_member_id") or request_data["member_id"])
            holder = policy.get("policy_holder", {})
            now = _now()
            connection.execute(
                """INSERT INTO benefit_reservations
                   (claim_id, family_account_id, member_id, category, policy_start, policy_end,
                    amount_paise, category_amount_paise, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)""",
                (claim_id, family_account_id, request_data["member_id"], request_data["claim_category"],
                 holder.get("policy_start_date", "0001-01-01"), holder.get("policy_end_date", "9999-12-31"),
                 amount_paise, amount_paise, now, now),
            )
        result = dict(before)
        result.update({"state": "DECIDED", "decision": decision, "approved_amount_paise": amount_paise, "approved_amount": to_rupees(amount_paise)})
        result.setdefault("reasons", []).append({"code": "REVIEWER_DISPOSITION", "message": reason_text})
        result.setdefault("trace", []).append({
            "stage": "manual_review", "rule_id": "reviewer_disposition", "status": decision,
            "evidence": {"approved_amount_paise": amount_paise, "reviewer_id": reviewer_id, "reason_code": reason_code, "evidence_summary": evidence_summary},
        })
        connection.execute(
            "INSERT INTO reviewer_actions (claim_id, occurred_at, reviewer_id, reason_code, reason_text, evidence_summary, before_json, after_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (claim_id, _now(), reviewer_id, reason_code, reason_text, evidence_summary, json.dumps(before, default=str), json.dumps(result, default=str)),
        )
        _set_state_in_connection(connection, claim_id, "DECIDED", result=result, detail={"decision": decision, "source": "reviewer_disposition", "reviewer_id": reviewer_id})
    return _load_claim(claim_id) or result


@app.post("/api/claims/{claim_id}/settlement")
def update_settlement(request: Request, claim_id: str, update: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Move a reserved benefit to PAID or RELEASED with an immutable event."""
    actor_id = _require_reviewer(request)
    target = str(update.get("status") or "").upper()
    reason_code = str(update.get("reason_code") or "").strip().upper()
    reason_text = str(update.get("reason_text") or "").strip()
    if target not in {"PAID", "RELEASED"} or not re.fullmatch(r"[A-Z0-9_]{3,60}", reason_code) or not reason_text:
        raise _input_error("Settlement status, reason_code, and reason_text are required.")
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status FROM benefit_reservations WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="This claim has no reserved benefit.")
        current = str(row["status"])
        allowed = (current == "RESERVED" and target in {"PAID", "RELEASED"}) or (current == "PAID" and target == "RELEASED")
        if not allowed:
            raise HTTPException(status_code=409, detail=f"Settlement cannot move from {current} to {target}.")
        now = _now()
        connection.execute("UPDATE benefit_reservations SET status=?, updated_at=? WHERE claim_id=?", (target, now, claim_id))
        connection.execute(
            "INSERT INTO settlement_events (claim_id, occurred_at, actor_id, from_status, to_status, reason_code, reason_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (claim_id, now, actor_id, current, target, reason_code, reason_text),
        )
        _record_event(connection, claim_id, "SETTLEMENT", {"from": current, "to": target, "actor_id": actor_id, "reason_code": reason_code})
    saved = _load_claim(claim_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return saved


@app.get("/api/claims")
def list_claims(request: Request, limit: int = 20) -> dict[str, Any]:
    _require_reviewer(request)
    if not 1 <= limit <= 100:
        raise _input_error("Limit must be between 1 and 100.")
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, created_at, state, request_json, result_json FROM claims ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "claims": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "state": row["state"],
                "request": json.loads(row["request_json"]),
                "decision": json.loads(row["result_json"]).get("decision") if row["result_json"] else None,
            }
            for row in rows
        ]
    }


@app.post("/api/claims/{claim_id}/retry", status_code=202)
def retry_claim(claim_id: str, background_tasks: BackgroundTasks) -> JSONResponse:
    claim = _load_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["state"] != "PROCESSING_FAILED":
        raise HTTPException(status_code=409, detail="Only a failed processing job can be retried.")
    _set_state(claim_id, "QUEUED", detail={"message": "Retry requested"})
    background_tasks.add_task(process_claim, claim_id)
    return JSONResponse({"id": claim_id, "state": "QUEUED", "url": f"/claims/{claim_id}"}, status_code=202)
