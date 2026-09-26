"""Bounded specialist handoffs for evidence and claim decisions.

Models can propose document facts only. Every handoff is checked before the
deterministic policy evaluator receives evidence or a decision is persisted.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Callable

from claims.ai_review import _date_supported, _money_supported, _type_supported
from claims.documents import VALID_TYPES, apply_evidence_candidates, revalidate_documents

EvidenceResolver = Callable[..., dict[str, Any]]
PolicyEvaluator = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
DECISIONS = {"APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW"}
DECISION_FIELDS = {
    "state", "decision", "approved_amount", "approved_amount_paise", "reasons",
    "correction_requests", "confidence_score", "trace", "ledger",
}
TRACE_FIELDS = {
    "patient_name", "date", "diagnosis", "treatment", "doctor_name",
    "doctor_registration", "hospital_name", "test_name", "tests_ordered",
    "total", "line_items", "bill_number", "medicines",
}
CANDIDATE_FIELDS = {
    "document_type", "patient_name", "date", "diagnosis", "hospital_name",
    "test_name", "total_paise", "line_items",
}
SAFE_MODEL_REASONS = {
    "provider_disabled", "provider_not_configured", "provider_dependency_unavailable",
    "provider_unavailable", "provider_error", "rate_limited", "timeout",
    "connection_error", "model_abstained", "invalid_json_response",
    "response_validation_failed", "source_text_unavailable", "relevant_page_unavailable",
    "identity_conflict", "identity_mismatch", "requested_evidence_unresolved",
    "source_quote_not_found", "amount_not_supported_by_quote", "date_not_supported_by_quote",
    "line_item_total_conflict", "input_preparation_failed", "invalid_provider_input",
    "file_cap_exceeded", "claim_page_cap_exceeded", "document_page_cap_exceeded",
    "resolver_failure", "candidate_application_failed", "invalid_handoff_envelope",
}
METRIC_FIELDS = {"calls", "retries", "files", "pages", "input_tokens", "output_tokens", "total_tokens"}


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _quote_supports_money(amount_paise: Any, quote: str, *, total: bool) -> bool:
    try:
        _money_supported(amount_paise, quote, total=total)
        return True
    except (TypeError, ValueError):
        return False


def _quote_supports_date(value: Any, quote: str) -> bool:
    try:
        return _date_supported(str(value), quote) == value
    except (TypeError, ValueError):
        return False


def _quote_supports_type(value: Any, quote: str) -> bool:
    try:
        _type_supported(str(value), quote)
        return True
    except (TypeError, ValueError):
        return False


def _candidate_is_grounded(
    candidate: dict[str, Any], documents_by_id: dict[str, dict[str, Any]],
    ocr_text_by_file_id: dict[str, Any], allowed_patient_names: list[str],
) -> bool:
    file_id = str(candidate.get("file_id"))
    document = documents_by_id.get(file_id)
    fields = candidate.get("fields")
    evidence = candidate.get("evidence")
    if document is None or not isinstance(fields, dict) or not fields or not isinstance(evidence, list):
        return False
    if not set(fields) <= CANDIDATE_FIELDS:
        return False
    source = ocr_text_by_file_id.get(file_id)
    pages = list(source) if isinstance(source, (list, tuple)) else source.split("\f") if isinstance(source, str) else []
    if not pages or len(pages) > int(document.get("pages") or 1):
        return False
    proven_fields: set[str] = set()
    proofs_by_field: dict[str, list[str]] = {}
    for proof in evidence:
        if not isinstance(proof, dict) or proof.get("field") not in fields:
            return False
        page = proof.get("page")
        quote = proof.get("quote")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(pages):
            return False
        if not isinstance(quote, str) or not 1 <= len(quote) <= 300:
            return False
        if _normalized(quote) not in _normalized(str(pages[page - 1])):
            return False
        field = str(proof["field"])
        proven_fields.add(field)
        proofs_by_field.setdefault(field, []).append(quote)
    if set(fields) != proven_fields:
        return False
    for field, value in fields.items():
        quotes = proofs_by_field[field]
        if field in {"patient_name", "diagnosis", "hospital_name", "test_name"} and (
            not isinstance(value, str) or not value.strip() or len(value) > 500
        ):
            return False
        if field == "document_type" and not any(_quote_supports_type(value, quote) for quote in quotes):
            return False
        if field == "date" and not any(_quote_supports_date(value, quote) for quote in quotes):
            return False
        if field == "total_paise" and not any(_quote_supports_money(value, quote, total=True) for quote in quotes):
            return False
        if field == "line_items":
            if not isinstance(value, list) or not all(
                isinstance(item, dict) and any(
                    _normalized(str(item.get("description"))) in _normalized(quote)
                    and _quote_supports_money(item.get("amount_paise"), quote, total=False)
                    for quote in quotes
                ) for item in value
            ):
                return False
        if field not in {"document_type", "date", "total_paise", "line_items"} and not any(
            _normalized(str(value)) in _normalized(quote) for quote in quotes
        ):
            return False
    if "document_type" in fields:
        kind = fields["document_type"]
        current_type = str(document.get("actual_type") or "UNKNOWN").upper()
        if kind not in VALID_TYPES or kind == "UNKNOWN" or current_type not in {"UNKNOWN", kind}:
            return False
    if "patient_name" in fields and _normalized(str(fields["patient_name"])) not in {
        _normalized(name) for name in allowed_patient_names
    }:
        return False
    if "date" in fields:
        try:
            date.fromisoformat(fields["date"])
        except (TypeError, ValueError):
            return False
    if "total_paise" in fields and (
        isinstance(fields["total_paise"], bool)
        or not isinstance(fields["total_paise"], int)
        or fields["total_paise"] <= 0
    ):
        return False
    line_items = fields.get("line_items")
    if line_items is not None:
        if not isinstance(line_items, list) or not line_items:
            return False
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("description"), str)
            or not item["description"].strip()
            or isinstance(item.get("amount_paise"), bool)
            or not isinstance(item.get("amount_paise"), int)
            or item["amount_paise"] <= 0
            for item in line_items
        ):
            return False
        if "total_paise" in fields and sum(item["amount_paise"] for item in line_items) != fields["total_paise"]:
            return False
    return True


def _safe_candidate_is_grounded(
    candidate: dict[str, Any], documents_by_id: dict[str, dict[str, Any]],
    ocr_text_by_file_id: dict[str, Any], allowed_patient_names: list[str],
) -> bool:
    try:
        return _candidate_is_grounded(
            candidate, documents_by_id, ocr_text_by_file_id, allowed_patient_names
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _bounded_fact(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= 2:
        return "Additional detail omitted"
    if isinstance(value, list):
        return [_bounded_fact(item, depth + 1) for item in value[:40]]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded_fact(item, depth + 1) for key, item in list(value.items())[:20]}
    return str(value)[:500]


def document_evidence_trace(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep bounded, relevant extracted facts so a reviewer can audit the decision."""
    records = []
    for document in documents:
        content = document.get("content") or document.get("fields") or {}
        records.append({
            "file_id": document.get("file_id"),
            "document_type": document.get("actual_type") or document.get("doc_type"),
            "quality": document.get("quality"),
            "extraction_source": document.get("extraction_source"),
            "confidence": document.get("confidence"),
            "fields": {name: _bounded_fact(value) for name, value in content.items() if name in TRACE_FIELDS},
            "sources": [
                {
                    "field": entry.get("field"),
                    "source": entry.get("source"),
                    "page": entry.get("page"),
                    "snippet": str(entry.get("snippet") or "")[:180],
                }
                for entry in (document.get("evidence") or [])[:30]
                if isinstance(entry, dict)
            ],
        })
    return {
        "stage": "document_evidence",
        "rule_id": "extracted_facts",
        "status": "RECORDED",
        "schema_version": 1,
        "evidence": records,
    }


def resolve_document_handoff(
    inspection: dict[str, Any],
    files_by_id: dict[str, dict[str, Any]],
    ocr_text_by_file_id: dict[str, Any],
    claim_category: str,
    member_name: str,
    allowed_patient_names: list[str],
    policy: dict[str, Any],
    resolver: EvidenceResolver,
) -> dict[str, Any]:
    """Validate Gemini's envelope, then re-run the deterministic document gate.

    Returns documents, issues, metrics and trace. Raw OCR text is never returned.
    Invalid provider output is an abstention; it cannot replace local evidence.
    """
    documents = inspection.get("documents", [])
    issues = inspection.get("issues", [])
    metrics = dict(inspection.get("metrics", {}))
    try:
        response = resolver(
            documents,
            files_by_id,
            ocr_text_by_file_id,
            allowed_patient_names=allowed_patient_names,
        )
    except Exception as exc:  # noqa: BLE001 - redact provider errors at the handoff boundary
        response = {
            "schema_version": 1,
            "producer": "gemini_evidence",
            "task": "resolve_document_facts",
            "source_file_ids": [],
            "status": "ABSTAINED",
            "candidates": [],
            "trace": {
                "stage": "gemini_evidence", "status": "ABSTAINED",
                "reason": "resolver_failure", "error_type": type(exc).__name__,
            },
            "metrics": {"calls": 0, "pages": 0},
        }
    status = response.get("status") if isinstance(response, dict) else None
    raw_candidates = response.get("candidates") if isinstance(response, dict) else None
    candidate_list: list[Any] = raw_candidates if isinstance(raw_candidates, list) else []
    documents_by_id = {str(document.get("file_id")): document for document in documents}
    expected_ids = set(documents_by_id)
    valid_envelope = (
        isinstance(response, dict)
        and response.get("schema_version") == 1
        and response.get("producer") == "gemini_evidence"
        and response.get("task") == "resolve_document_facts"
        and isinstance(response.get("source_file_ids"), list)
        and all(isinstance(file_id, str) and file_id in expected_ids for file_id in response["source_file_ids"])
        and len(set(response["source_file_ids"])) == len(response["source_file_ids"])
        and status in {"NOT_NEEDED", "ABSTAINED", "CANDIDATES_VALIDATED"}
        and isinstance(raw_candidates, list)
        and isinstance(response.get("metrics"), dict)
        and isinstance(response.get("trace"), dict)
        and response["trace"].get("stage") == "gemini_evidence"
        and response["trace"].get("status") == status
        and (status == "CANDIDATES_VALIDATED" or not candidate_list)
    )
    if valid_envelope and status == "CANDIDATES_VALIDATED":
        valid_envelope = bool(candidate_list) and all(
            isinstance(candidate, dict)
            and str(candidate.get("file_id")) in response["source_file_ids"]
            and _safe_candidate_is_grounded(
                candidate, documents_by_id, ocr_text_by_file_id, allowed_patient_names
            )
            for candidate in candidate_list
        ) and (
            len({str(candidate["file_id"]) for candidate in candidate_list}) == len(candidate_list)
            and {str(candidate["file_id"]) for candidate in candidate_list} == set(response["source_file_ids"])
        )
    if not valid_envelope:
        status = "ABSTAINED"
        response = {
            "schema_version": 1,
            "producer": "gemini_evidence",
            "task": "resolve_document_facts",
            "source_file_ids": [],
            "status": status,
            "candidates": [],
            "trace": {"stage": "gemini_evidence", "status": status, "reason": "invalid_handoff_envelope"},
            "metrics": {"calls": 0, "pages": 0},
        }

    safe_metrics = {
        key: value for key, value in response["metrics"].items()
        if key in METRIC_FIELDS and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000_000
    }
    metrics["gemini"] = {**safe_metrics, "status": status}
    raw_reason = response["trace"].get("reason")
    trace = {
        "stage": "gemini_evidence",
        "status": status,
        "reason": raw_reason if isinstance(raw_reason, str) and raw_reason in SAFE_MODEL_REASONS else None if raw_reason is None else "provider_response_unavailable",
    }
    trace["schema_version"] = 1
    if status == "CANDIDATES_VALIDATED":
        try:
            updated_documents = apply_evidence_candidates(
                documents, candidate_list, ocr_text_by_file_id
            )
            updated_issues = revalidate_documents(
                updated_documents, issues, claim_category, member_name, policy,
                allowed_patient_names=allowed_patient_names,
            )
        except Exception as exc:  # noqa: BLE001 - a bad candidate must preserve original evidence
            return {
                "documents": documents,
                "issues": issues,
                "metrics": {**metrics, "gemini": {**safe_metrics, "status": "ABSTAINED"}},
                "trace": [{
                    "stage": "gemini_evidence", "status": "ABSTAINED",
                    "reason": "candidate_application_failed", "error_type": type(exc).__name__,
                    "schema_version": 1,
                }],
                "status": "ABSTAINED",
            }
        documents = updated_documents
        issues = updated_issues
        trace["status"] = "CANDIDATES_APPLIED"
        trace["candidate_evidence"] = [
            {
                "file_id": candidate.get("file_id"),
                "fields": sorted(candidate["fields"]),
                "sources": [
                    {"field": proof.get("field"), "page": proof.get("page"), "quote": proof.get("quote")}
                    for proof in candidate["evidence"]
                ],
            }
            for candidate in candidate_list
        ]
    return {
        "documents": documents,
        "issues": issues,
        "metrics": metrics,
        "trace": [trace],
        "status": status,
    }


def adjudicate_handoff(
    payload: dict[str, Any], policy: dict[str, Any], evaluator: PolicyEvaluator
) -> dict[str, Any]:
    """Apply the policy agent and reject malformed or inconsistent decisions."""
    try:
        result = evaluator(payload, policy)
    except Exception as exc:  # noqa: BLE001 - one failed specialist must not create a payable decision
        return _manual_review_handoff("policy_agent_failure", type(exc).__name__)
    if _decision_is_consistent(result, payload):
        return result
    return _manual_review_handoff("invalid_decision_handoff", None)


def _manual_review_handoff(reason: str, error_type: str | None) -> dict[str, Any]:
    return {
        "state": "MANUAL_REVIEW",
        "decision": "MANUAL_REVIEW",
        "approved_amount": 0,
        "approved_amount_paise": 0,
        "reasons": [{"code": "INVALID_DECISION_HANDOFF", "message": "An operator must review the claim decision."}],
        "correction_requests": [],
        "confidence_score": 0.0,
        "ledger": [],
        "trace": [{
            "stage": "decision_validation",
            "rule_id": "decision_handoff_contract",
            "status": "FAIL",
            "reason": reason,
            "error_type": error_type,
            "details": "The policy result was malformed or financially inconsistent; no amount was approved.",
        }],
    }


def _decision_is_consistent(result: Any, payload: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if set(result) != DECISION_FIELDS:
        return False
    decision = result.get("decision")
    if decision not in DECISIONS and decision is not None:
        return False
    if not isinstance(result.get("reasons"), list) or not isinstance(result.get("trace"), list):
        return False
    if not isinstance(result.get("ledger"), list) or not isinstance(result.get("correction_requests"), list):
        return False
    if not all(isinstance(item, dict) for item in result["reasons"] + result["trace"] + result["ledger"]):
        return False
    if not all(
        isinstance(item.get("code"), str) and item["code"].strip()
        and isinstance(item.get("message"), str) and item["message"].strip()
        for item in result["reasons"]
    ):
        return False
    if not all(
        isinstance(item.get("stage"), str) and item["stage"].strip()
        and isinstance(item.get("rule_id"), str) and item["rule_id"].strip()
        and isinstance(item.get("status"), str) and item["status"].strip()
        for item in result["trace"]
    ):
        return False
    if not result["trace"]:
        return False
    amount = result.get("approved_amount_paise")
    rupees = result.get("approved_amount")
    if decision is None:
        return (
            amount is None and rupees is None
            and bool(result["correction_requests"])
            and all(
                isinstance(item, dict)
                and isinstance(item.get("code"), str) and item["code"].strip()
                and isinstance(item.get("message"), str) and item["message"].strip()
                for item in result["correction_requests"]
            )
            and result.get("state") in {
            "NEEDS_CORRECTION", "DOCUMENT_CORRECTION_REQUIRED"
            }
        )
    if not result["reasons"] or result["correction_requests"]:
        return False
    if result.get("state") != ("MANUAL_REVIEW" if decision == "MANUAL_REVIEW" else "DECIDED"):
        return False
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        return False
    try:
        claimed_paise = Decimal(str(payload["claimed_amount"])) * 100
        stated_paise = Decimal(str(rupees)) * 100
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return False
    if claimed_paise < 0 or stated_paise != amount or amount > claimed_paise:
        return False
    if decision in {"REJECTED", "MANUAL_REVIEW"} and amount != 0:
        return False
    if decision in {"APPROVED", "PARTIAL"} and amount == 0:
        return False
    if decision in {"APPROVED", "PARTIAL"}:
        if not result["ledger"]:
            return False
        ledger_total = 0
        for item in result["ledger"]:
            item_amount = item.get("amount_paise")
            description = item.get("description")
            if isinstance(item_amount, bool) or not isinstance(item_amount, int):
                return False
            if not isinstance(description, str) or not description.strip():
                return False
            # Only payable lines and adjustments make up the approved amount;
            # excluded, not-covered or otherwise unpaid lines are itemized for
            # the member but contribute nothing.
            if item.get("kind") == "line_item" and item.get("status") != "ELIGIBLE":
                continue
            ledger_total += item_amount
        if ledger_total != amount:
            return False
    score = result.get("confidence_score")
    return isinstance(score, (int, float)) and not isinstance(score, bool) and isfinite(score) and 0 <= score <= 1
