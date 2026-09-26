"""Local reviewer interface and durable upload workflow for OPD claims."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

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
from claims.core import evaluate_claim
from claims.documents import process_uploads

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


def _now() -> str:
    test_now = os.getenv("PLUM_TEST_NOW")
    if test_now:
        try:
            return datetime.fromisoformat(test_now.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError as exc:
            raise RuntimeError("PLUM_TEST_NOW must be an ISO-8601 timestamp") from exc
    return datetime.now(timezone.utc).isoformat()


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
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE claims ADD COLUMN {name} {definition}")
        # These indexed fields make duplicate checks and member benefit history
        # a bounded lookup instead of reparsing every saved claim on each request.
        connection.execute("CREATE INDEX IF NOT EXISTS idx_claims_member_treatment ON claims(member_id, treatment_date, state)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_documents_hash_claim ON documents(sha256, claim_id)")
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
) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE claims SET state=?, updated_at=?, result_json=?, error_message=?, decision=?, approved_amount_paise=? WHERE id=?",
            (
                state,
                _now(),
                json.dumps(result, default=str) if result is not None else None,
                error,
                result.get("decision") if result else None,
                int(result.get("approved_amount_paise") or 0) if result else 0,
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


def _read_policy() -> dict[str, Any]:
    with (PROJECT_ROOT / "data" / "policy_terms.json").open(encoding="utf-8") as file:
        return json.load(file)


def _member_name(policy: dict[str, Any], member_id: str) -> str:
    for member in policy.get("members", []):
        if member.get("member_id") == member_id:
            return str(member.get("name", ""))
    return ""


def _covered_member_names(policy: dict[str, Any], member_id: str) -> list[str]:
    members = policy.get("members", [])
    member = next((item for item in members if item.get("member_id") == member_id), None)
    if member is None:
        return []
    owner_id = str(member.get("primary_member_id") or member_id)
    owner = next((item for item in members if item.get("member_id") == owner_id), member)
    covered_ids = {owner_id, *[str(value) for value in owner.get("dependents", [])]}
    return [str(item.get("name")) for item in members if item.get("member_id") in covered_ids and item.get("name")]


def _gemini_opt_in() -> bool:
    return os.getenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "false").strip().casefold() in {"1", "true", "yes"}


def _gemini_provider_failure(result: dict[str, Any]) -> bool:
    return result.get("status") == "ABSTAINED" and any(
        marker in str((result.get("trace") or {}).get("reason") or "")
        for marker in ("timeout", "connection_error", "provider_error", "rate_limited", "provider_unavailable", "provider_not_configured", "provider_dependency_unavailable", "resolver_failure", "candidate_application_failed")
    )


def _media_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    actual_type = _media_type(data)
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


def _member_claim_history(
    claim_id: str, request_data: dict[str, Any], policy: dict[str, Any]
) -> tuple[list[dict[str, str]], int]:
    """Read the covered employee's policy-year claims for risk and limit checks."""
    member_id = str(request_data["member_id"])
    member = next((item for item in policy.get("members", []) if item.get("member_id") == member_id), None)
    if member is None:
        return [], 0
    owner_id = str(member.get("primary_member_id") or member_id)
    owner = next((item for item in policy.get("members", []) if item.get("member_id") == owner_id), member)
    covered_ids = {owner_id, *[str(value) for value in owner.get("dependents", [])]}
    start = str(policy.get("policy_holder", {}).get("policy_start_date", "0001-01-01"))
    end = str(policy.get("policy_holder", {}).get("policy_end_date", "9999-12-31"))
    with _connect() as connection:
        rows = connection.execute(
            """SELECT id, treatment_date, state, decision, approved_amount_paise
               FROM claims WHERE id<>? AND member_id=? AND treatment_date BETWEEN ? AND ?
                 AND state='DECIDED' AND decision IN ('APPROVED', 'PARTIAL')
                 AND approved_amount_paise>0 ORDER BY created_at""",
            (claim_id, member_id, start, end),
        ).fetchall()
    history = [{"date": row["treatment_date"]} for row in rows if row["treatment_date"]]
    # The prototype has adjudication records but no insurer remittance feed.
    # Approved amounts are therefore the best available consumed-benefit proxy;
    # the trace labels them as adjudicated amounts rather than paid reimbursements.
    marks = ",".join("?" for _ in covered_ids)
    with _connect() as connection:
        benefit_rows = connection.execute(
            f"""SELECT approved_amount_paise FROM claims WHERE id<>? AND member_id IN ({marks})
                 AND treatment_date BETWEEN ? AND ? AND state='DECIDED'
                 AND decision IN ('APPROVED', 'PARTIAL')""",
            (claim_id, *sorted(covered_ids), start, end),
        ).fetchall()
    approved_ytd_paise = sum(int(row["approved_amount_paise"] or 0) for row in benefit_rows)
    return history, approved_ytd_paise


def _bill_fingerprints(request_data: dict[str, Any], inspected_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable evidence keys for reprinted bills; all four facts must be present."""
    fingerprints: list[dict[str, Any]] = []
    for document in inspected_documents:
        if not str(document.get("actual_type") or document.get("doc_type") or "").upper().endswith("BILL"):
            continue
        fields = document.get("content") or document.get("fields") or {}
        bill_number = re.sub(r"[^a-z0-9]", "", str(fields.get("bill_number") or "").casefold())
        provider = re.sub(r"[^a-z0-9]", "", str(fields.get("hospital_name") or "").casefold())
        total = fields.get("total")
        if not bill_number or not provider or total is None:
            continue
        try:
            total_paise = int(Decimal(str(total)) * 100)
        except (InvalidOperation, ValueError):
            continue
        fingerprints.append({
            "bill_number": bill_number, "provider": provider, "total_paise": total_paise,
            "treatment_date": str(request_data.get("treatment_date") or ""),
        })
    return fingerprints


def _save_bill_fingerprints(claim_id: str, fingerprints: list[dict[str, Any]]) -> None:
    with _connect() as connection:
        connection.execute("UPDATE claims SET bill_fingerprints_json=? WHERE id=?", (json.dumps(fingerprints), claim_id))


def _duplicate_bill_hits(
    claim_id: str, request_data: dict[str, Any], inspected_documents: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find paid exact-file or evidenced logical bill duplicates."""
    bill_hashes = {
        str(document.get("sha256"))
        for document in inspected_documents
        if str(document.get("actual_type") or document.get("doc_type") or "").upper().endswith("BILL")
        and document.get("sha256")
    }
    hits: list[dict[str, Any]] = []
    with _connect() as connection:
        for digest in sorted(bill_hashes):
            rows = connection.execute(
                """SELECT DISTINCT documents.claim_id
                   FROM documents JOIN claims ON claims.id=documents.claim_id
                   WHERE documents.sha256=? AND documents.claim_id<>?
                     AND claims.state='DECIDED'
                     AND claims.decision IN ('APPROVED', 'PARTIAL')
                     AND claims.approved_amount_paise>0
                   ORDER BY documents.claim_id""",
                (digest, claim_id),
            ).fetchall()
            if rows:
                hits.append({"previous_claim_ids": [row["claim_id"] for row in rows]})
        fingerprints = _bill_fingerprints(request_data, inspected_documents)
        if fingerprints:
            rows = connection.execute(
                """SELECT id, bill_fingerprints_json FROM claims
                   WHERE id<>? AND state='DECIDED' AND decision IN ('APPROVED', 'PARTIAL')
                     AND approved_amount_paise>0""",
                (claim_id,),
            ).fetchall()
            logical_ids = []
            for row in rows:
                saved = json.loads(row["bill_fingerprints_json"] or "[]")
                if any(item in saved for item in fingerprints):
                    logical_ids.append(row["id"])
            if logical_ids:
                hits.append({"previous_claim_ids": logical_ids, "match_type": "logical_bill_fingerprint"})
    return hits


def _duplicate_review_result(hits: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    prior_ids = sorted({claim_id for hit in hits for claim_id in hit["previous_claim_ids"]})
    return {
        "state": "MANUAL_REVIEW",
        "decision": "MANUAL_REVIEW",
        "approved_amount": 0,
        "approved_amount_paise": 0,
        "reasons": [{"code": "DUPLICATE_BILL", "message": "A previously paid claim has an identical bill file or matching bill number, provider, treatment date, and amount. Verify that the expense has not already been reimbursed."}],
        "correction_requests": [],
        "confidence_score": 0.2,
        "ledger": [],
        "trace": [{"stage": "duplicate_check", "rule_id": "previous_bill_match", "status": "FLAG", "evidence": {"matching_claim_count": len(prior_ids), "matching_claim_ids": prior_ids}, "details": "Exact-file and complete logical-bill fingerprints are checked against paid claims."}],
        "document_metrics": metrics,
    }


def process_claim(claim_id: str) -> None:
    """Run one persisted claim; safe to retry after process restart."""
    claim = _load_claim(claim_id)
    if claim is None or claim["state"] not in {"QUEUED", "PROCESSING"}:
        return
    _set_state(claim_id, "PROCESSING", detail={"message": "Checking submitted documents"})
    try:
        policy = _read_policy()
        request_policy_hash = str((claim.get("request") or {}).get("policy_sha256") or "")
        current_policy_hash = hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()
        if request_policy_hash and request_policy_hash != current_policy_hash:
            snapshot_result: dict[str, Any] = {
                "state": "MANUAL_REVIEW",
                "decision": "MANUAL_REVIEW",
                "approved_amount": 0,
                "approved_amount_paise": 0,
                "reasons": [{"code": "POLICY_CHANGED", "message": "The policy changed after this claim was submitted. A reviewer must re-evaluate it against the current policy."}],
                "correction_requests": [],
                "confidence_score": 0.0,
                "ledger": [],
                "trace": [{"stage": "policy", "rule_id": "policy_snapshot", "status": "FAIL", "policy_ref": "claim.policy_sha256", "details": "The policy snapshot captured at intake does not match the current policy."}],
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
        request_data = claim["request"]
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
        fingerprints = _bill_fingerprints(request_data, inspection.get("documents", []))
        _save_bill_fingerprints(claim_id, fingerprints)
        duplicate_hits = _duplicate_bill_hits(claim_id, request_data, inspection.get("documents", []))
        if duplicate_hits:
            result = _duplicate_review_result(duplicate_hits, inspection.get("metrics", {}))
            _set_state(
                claim_id,
                "MANUAL_REVIEW",
                result=result,
                detail={"duplicate_bill_match_count": sum(len(hit["previous_claim_ids"]) for hit in duplicate_hits)},
            )
            return
        payload = dict(request_data)
        payload["documents"] = inspection.get("documents", [])
        claims_history, approved_ytd_paise = _member_claim_history(claim_id, request_data, policy)
        payload["claims_history"] = claims_history
        payload["claims_history_source"] = "local_claim_database"
        payload["ytd_claims_amount"] = approved_ytd_paise / 100
        payload["ytd_claims_source"] = "database_approved_decisions"
        result = adjudicate_handoff(payload, policy, evaluate_claim)
        result.setdefault("document_metrics", inspection.get("metrics", {}))
        result["trace"] = [document_evidence_trace(inspection.get("documents", []))] + gemini_trace + result.get("trace", [])
        final_state = str(result.get("state") or result.get("decision") or "MANUAL_REVIEW")
        _set_state(claim_id, final_state, result=result, detail={"decision": result.get("decision")})
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


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    policy = _read_policy()
    members = [{"id": member["member_id"], "name": member["name"]} for member in policy.get("members", [])]
    return templates.TemplateResponse(request, "index.html", {"members": members, "categories": CATEGORIES})


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
        amount = Decimal(claimed_amount)
        if not amount.is_finite() or amount <= 0 or amount > 1_000_000 or amount * 100 != (amount * 100).to_integral_value():
            raise InvalidOperation
        amount_paise = int(amount * 100)
    except (InvalidOperation, ValueError) as exc:
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
        "policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest(),
        "claim_category": claim_category,
        "treatment_date": treatment_date,
        "claimed_amount": float(Decimal(amount_paise) / 100),
        "submission_date": now[:10],
    }
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
        amount = Decimal(str(disposition.get("approved_amount", 0)))
        if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
            raise InvalidOperation
        amount_paise = int(amount * 100)
    except (InvalidOperation, ValueError) as exc:
        raise _input_error("Reviewer approved amount must be a valid amount.") from exc
    claimed_paise = int(Decimal(str(claim["request"]["claimed_amount"])) * 100)
    if amount_paise < 0 or amount_paise > claimed_paise or (decision == "REJECTED" and amount_paise != 0) or (decision != "REJECTED" and amount_paise <= 0):
        raise _input_error("Reviewer amount is inconsistent with the requested decision or claim amount.")
    result = dict(claim["result"] or {})
    result.update({"state": "DECIDED", "decision": decision, "approved_amount_paise": amount_paise, "approved_amount": amount_paise / 100})
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
