"""Local reviewer interface and durable upload workflow for OPD claims."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone, tzinfo
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
            CREATE INDEX IF NOT EXISTS idx_claim_events_claim ON claim_events(claim_id, id);
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


# A claim counts toward the member's claim history (same-day and monthly
# frequency limits) only once it has been submitted as a valid claim: either the
# policy engine adjudicated it (DECIDED, or MANUAL_REVIEW with adjudicated=1), or a
# reviewer recorded a final decision. Uploads stopped at the document gate
# (DOCUMENT_CORRECTION_REQUIRED), provider outages and duplicate-bill holds awaiting
# review, queued/processing jobs, and failed jobs never count: they are attempts to
# submit, not claims. Rejected claims do count; they were real submissions.
COUNTED_CLAIM_SQL = "(state='DECIDED' OR (state='MANUAL_REVIEW' AND adjudicated=1))"
# Benefit usage is the sum of approved amounts on decided payable claims. The
# prototype has no insurer remittance feed, so adjudicated approvals stand in for
# paid reimbursements, and the trace labels the figure's source accordingly.
PAYABLE_CLAIM_SQL = "(state='DECIDED' AND decision IN ('APPROVED', 'PARTIAL') AND approved_amount_paise>0)"


def _member_claim_history(claim_id: str, request_data: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
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
    with _connect() as connection:
        rows = connection.execute(
            f"""SELECT id, member_id, treatment_date, request_json, result_json, approved_amount_paise,
                       {PAYABLE_CLAIM_SQL} AS payable
                FROM claims WHERE id<>? AND member_id IN ({marks}) AND treatment_date BETWEEN ? AND ?
                  AND {COUNTED_CLAIM_SQL}
                ORDER BY created_at""",
            (claim_id, *family_ids, start, end),
        ).fetchall()
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
        if not row["payable"]:
            continue
        approved = int(row["approved_amount_paise"] or 0)
        usage["family_approved_paise"] += approved
        if row["member_id"] != member_id or prior_request.get("claim_category") != category:
            continue
        usage["member_category_approved_paise"] += approved
        sub_limit_step = next((step for step in prior_result.get("trace", []) if step.get("rule_id") == "category_sub_limit"), None)
        counted = (sub_limit_step or {}).get("evidence", {}).get("counted_against_sub_limit_paise")
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
    keyed by bill number, provider, and total (plus its printed date when readable);
    an unnumbered bill needs provider, total, printed date, and patient name.
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
                "total_paise": total_paise, "document_date": document_date,
            })
        elif document_date and patient:
            fingerprints.append({
                "kind": "unnumbered_bill", "provider": provider, "total_paise": total_paise,
                "document_date": document_date, "patient": patient,
            })
    return fingerprints


def _fingerprints_match(new: dict[str, Any], saved: dict[str, Any]) -> bool:
    kind = str(saved.get("kind") or "bill_number")  # rows from earlier versions carry no kind
    if kind != new.get("kind"):
        return False
    if kind == "bill_number":
        if any(new.get(key) != saved.get(key) for key in ("bill_number", "provider", "total_paise")):
            return False
        # A missing printed date cannot distinguish two bills; only two readable, different dates can.
        new_date, saved_date = new.get("document_date"), saved.get("document_date")
        return new_date is None or saved_date is None or new_date == saved_date
    return all(new.get(key) == saved.get(key) for key in ("provider", "total_paise", "document_date", "patient"))


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
        "reasons": [{"code": "DUPLICATE_BILL", "message": "Another paid or pending claim has an identical bill file, or a bill with the same bill number, provider, and amount (and no different printed date). Verify that the expense has not already been reimbursed."}],
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
        payload = dict(request_data)
        payload["documents"] = documents
        usage = _member_claim_history(claim_id, request_data, policy)
        family_used = to_rupees(usage["family_approved_paise"])
        payload["claims_history"] = usage["claims_history"]
        payload["claims_history_source"] = "local_family_submission_database"
        # Family pool (primary member + dependents), policy year, approved OPD decisions.
        payload["ytd_claims_amount"] = family_used
        payload["ytd_claims_source"] = "database_approved_decisions"
        payload["sum_insured_used"] = family_used
        payload["sum_insured_used_source"] = "database_approved_decisions"
        payload["family_floater_used"] = family_used
        payload["family_floater_used_source"] = "database_approved_decisions"
        # This member, this category, policy year, approved decisions.
        payload["category_ytd_claims_amount"] = to_rupees(usage["member_category_approved_paise"])
        payload["category_ytd_claims_source"] = "database_approved_decisions"
        if usage["member_category_sub_limit_paise"] is not None:
            payload["category_sub_limit_used"] = to_rupees(usage["member_category_sub_limit_paise"])
            payload["category_sub_limit_used_source"] = "database_decision_traces"
        payload["prior_sessions"] = usage["prior_sessions"]
        payload["prior_sessions_source"] = "database_approved_alternative_medicine_decisions"
        result = adjudicate_handoff(payload, policy, evaluate_claim)
        result.setdefault("provider", claim_provider(payload) or None)
        result.setdefault("document_metrics", inspection.get("metrics", {}))
        result["trace"] = [document_evidence_trace(documents)] + gemini_trace + result.get("trace", [])
        final_state = str(result.get("state") or result.get("decision") or "MANUAL_REVIEW")
        _set_state(claim_id, final_state, result=result, detail={"decision": result.get("decision")}, adjudicated=True)
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
    """Local reviewer worklist; production access control is not in this demo."""
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


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: str) -> dict[str, Any]:
    claim = _load_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@app.post("/api/claims/{claim_id}/review-decision")
def resolve_manual_review(claim_id: str, disposition: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Record a local reviewer disposition for a claim that was safely escalated."""
    claim = _load_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["state"] != "MANUAL_REVIEW":
        raise HTTPException(status_code=409, detail="Only a manual-review claim can receive a reviewer disposition.")
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
    claimed_paise = to_paise(claim["request"]["claimed_amount"])
    if amount_paise < 0 or amount_paise > claimed_paise or (decision == "REJECTED" and amount_paise != 0) or (decision != "REJECTED" and amount_paise <= 0):
        raise _input_error("Reviewer amount is inconsistent with the requested decision or claim amount.")
    result = dict(claim["result"] or {})
    result.update({"state": "DECIDED", "decision": decision, "approved_amount_paise": amount_paise, "approved_amount": to_rupees(amount_paise)})
    result.setdefault("reasons", []).append({"code": "REVIEWER_DISPOSITION", "message": "A reviewer recorded the final decision after inspecting the escalated claim."})
    result.setdefault("trace", []).append({"stage": "manual_review", "rule_id": "reviewer_disposition", "status": decision, "evidence": {"approved_amount_paise": amount_paise}})
    _set_state(claim_id, "DECIDED", result=result, detail={"decision": decision, "source": "reviewer_disposition"})
    return _load_claim(claim_id) or result


@app.get("/api/claims")
def list_claims(limit: int = 20) -> dict[str, Any]:
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
