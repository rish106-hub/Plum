"""Pure, deterministic OPD claim evaluation.

All money is converted to integer paise before arithmetic. The result keeps a
complete ordered trace, including rules that cannot be evaluated from evidence.

The engine reads only the canonical policy configuration produced by
``claims.policy``; it never reads the raw policy JSON. Evidence provenance
(fixture, PDF text, OCR, model-assisted extraction) is recorded in the trace but
never changes which rule applies or how it is decided.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable

from claims.policy import PolicyConfigurationError, ensure_canonical

OptionalRiskEnricher = Callable[[dict[str, Any]], None]

CONFIDENCE_BASE = 0.96
CONFIDENCE_NOTE = (
    "Heuristic evidence-completeness rubric, not a calibrated probability: the base score is reduced only by "
    "unknowns and degradations that are material to the outcome that was reached."
)
REJECT_PRIORITY = [
    "POLICY_NOT_ACTIVE", "RELATIONSHIP_NOT_COVERED", "CATEGORY_NOT_COVERED", "OUTSIDE_POLICY_PERIOD",
    "MINIMUM_CLAIM_AMOUNT", "EXCLUDED_CONDITION", "PRE_EXISTING_WAITING_PERIOD", "WAITING_PERIOD",
    "SESSION_LIMIT_EXCEEDED", "PRE_AUTH_MISSING", "PRE_AUTH_INVALID", "SUBMISSION_BEFORE_TREATMENT",
    "SUBMISSION_LATE", "PER_CLAIM_EXCEEDED",
]
REVIEW_CODES = {
    "SAME_DAY_CLAIMS", "MONTHLY_CLAIMS", "HIGH_VALUE_MANUAL_REVIEW", "FRAUD_SCORE_REVIEW",
    "LINE_ITEM_DESCRIPTION_UNKNOWN", "PHARMACY_BRAND_STATUS_UNKNOWN", "GENERIC_SUBSTITUTION_REVIEW",
    "PRE_AUTH_STATUS_UNKNOWN", "PRE_AUTH_CONFLICT", "PRE_AUTH_AMOUNT_UNVERIFIED", "COVERED_SYSTEM_UNKNOWN",
    "MEMBER_START_DATE_UNKNOWN", "TREATMENT_DATE_REQUIRED", "DOCUMENT_DATE_CONFLICT",
    "PRACTITIONER_REGISTRATION_UNKNOWN", "EXCLUSION_QUALIFIER_REVIEW",
}
PAYABLE = {"APPROVED", "PARTIAL"}


def _optional_risk_enrichment(_: dict[str, Any]) -> None:
    """Default optional hook; replace only with an internal risk component."""


def _paise(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid money amount: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Invalid money amount: {value!r}")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _rupees(paise: int) -> int | float:
    return paise // 100 if paise % 100 == 0 else paise / 100


def _percentage(paise: int, percent: Any) -> int:
    return int((Decimal(paise) * Decimal(str(percent)) / 100).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    ))


def _text(value: Any) -> str:
    return str(value or "").casefold().strip()


def _parse_document_date(value: Any) -> date:
    """Parse the explicit, unambiguous date formats emitted by local extraction."""
    raw = str(value or "").strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    for date_format in ("%d-%b-%Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported document date: {raw!r}")


def _normal_words(value: Any) -> str:
    return " ".join(re.findall(r"[^\W_]+", _text(value), flags=re.UNICODE))


def _normal_name(value: Any) -> str:
    """Compare patient names without routine Indian billing honorifics."""
    return " ".join(
        word for word in _normal_words(value).split()
        if word not in {"mr", "mrs", "ms", "miss", "dr", "shri", "smt"}
    )


def _contains_phrase(text: str, phrase: Any) -> bool:
    normalized_text = f" {_normal_words(text)} "
    normalized_phrase = _normal_words(phrase)
    return bool(normalized_phrase and f" {normalized_phrase} " in normalized_text)


def _affirmative(text: str, phrase: str) -> bool:
    """Find a phrase while ignoring an explicit nearby clinical negation."""
    normalized = _normal_words(text)
    needle = _normal_words(phrase)
    for match in re.finditer(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized) if needle else ():
        context = normalized[max(0, match.start() - 45):match.start()]
        if not re.search(r"\b(?:no|not|without|denies|denied|negative for)\b(?:\s+\w+){0,4}\s*$", context):
            return True
    return False


def _term_hits(text: str, terms: list[dict[str, str]], *, affirmative: bool = False) -> list[dict[str, str]]:
    matcher = _affirmative if affirmative else _contains_phrase
    return [term for term in terms if matcher(text, term["text"])]


def _exclusion_hits(text: str, exclusions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for exclusion in exclusions:
        matched = _term_hits(text, exclusion["terms"], affirmative=True)
        if matched:
            hits.append({
                "exclusion_id": exclusion["id"], "label": exclusion["label"],
                "matched_terms": matched, "qualifier": exclusion["qualifier"],
                "policy_text_match": any(term["provenance"] == "policy_text" for term in matched),
                "source_paths": exclusion["source_paths"],
            })
    return hits


def _rule_trace(
    rule_id: str, status: str, policy_ref: str, evidence: dict[str, Any], details: str | None = None
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "stage": "policy", "rule_id": rule_id, "status": status,
        "policy_ref": policy_ref, "evidence": evidence,
    }
    if details:
        step["details"] = details
    return step


def _fields(document: dict[str, Any]) -> dict[str, Any]:
    return document.get("fields") or document.get("content") or {}


def _doc_type(document: dict[str, Any]) -> str:
    return str(document.get("doc_type") or document.get("actual_type") or "").upper()


def _patient_name(document: dict[str, Any]) -> Any:
    return document.get("patient_name") or _fields(document).get("patient_name")


def _line_items(documents: list[dict[str, Any]], fallback_paise: int) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for document in documents:
        if not _doc_type(document).endswith("BILL"):
            continue
        fields = _fields(document)
        if fields.get("line_items"):
            lines.extend(
                {
                    "description": str(item.get("description") or ""),
                    "amount_paise": _paise(item.get("amount", 0)),
                    "brand_status": str(item.get("brand_status", "UNKNOWN")).upper(),
                    "brand_evidence": str(item.get("brand_evidence", "")),
                    "source_document": document.get("file_id"),
                }
                for item in fields["line_items"]
            )
    if lines:
        return lines
    return [{"description": "Claimed treatment", "amount_paise": fallback_paise, "brand_status": "UNKNOWN", "brand_evidence": "", "source_document": None}]


def _all_content(documents: list[dict[str, Any]]) -> str:
    parts = []
    for document in documents:
        fields = _fields(document)
        for name in ("diagnosis", "treatment", "test_name"):
            parts.append(str(fields.get(name, "")))
        parts.extend(str(test) for test in fields.get("tests_ordered", []))
        parts.extend(str(item.get("description", "")) for item in fields.get("line_items", []))
    return " ".join(parts).casefold()


def _network_match(hospital: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    """Apply the canonical ``exact_or_branch_suffix`` provider-name rule."""
    candidates = {_normal_words(hospital)}
    head = re.split(r",|\s[-–—]\s", hospital, maxsplit=1)[0]
    candidates.add(_normal_words(head))
    for entry in policy["network_hospitals"]:
        for term in entry["terms"]:
            if term["text"] in candidates:
                return {"network_hospital": entry["name"], "matched_term": term["text"], "provenance": term["provenance"], "policy_ref": entry["source_path"]}
    return None


def _policy_source_step(policy: dict[str, Any]) -> dict[str, Any]:
    audit = policy.get("audit", [])
    return {
        "stage": "configuration", "rule_id": "policy_source", "status": "LOADED", "policy_ref": "policy",
        "evidence": {
            "policy_id": policy["policy_id"],
            "schema_version": policy["schema_version"],
            "source_sha256": policy["source"]["sha256"],
            "canonical_sha256": policy.get("canonical_sha256"),
            "audit_entries": len(audit),
            "interpretation_entries": sum(1 for entry in audit if entry["kind"] == "interpretation"),
            "conflict_resolutions": [entry["id"] for entry in audit if entry["kind"] == "conflict_resolution"],
        },
        "details": "Canonical policy configuration; the complete normalizer audit trail is in policy['audit'].",
    }


def _document_gate(
    payload: dict[str, Any], policy: dict[str, Any], category: str, trace: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    documents = payload.get("documents") or []
    requirements = policy["document_requirements"].get(category, {})
    required = requirements.get("required", [])
    present = {_doc_type(doc) for doc in documents}
    corrections: list[dict[str, Any]] = []
    for required_type in required:
        if required_type not in present:
            uploaded = ", ".join(sorted(present)) or "no documents"
            corrections.append({
                "code": "DOCUMENT_MISSING",
                "required_type": required_type,
                "uploaded_types": sorted(present),
                "message": f"Uploaded {uploaded}; please upload a readable {required_type} for this {category.lower()} claim.",
            })
    for doc in documents:
        if _text(doc.get("quality", "GOOD")) in {"unreadable", "blurry", "illegible"}:
            kind = doc.get("doc_type") or doc.get("actual_type") or "document"
            corrections.append({
                "code": "DOCUMENT_UNREADABLE",
                "file_id": doc.get("file_id"),
                "required_type": kind,
                "message": f"The {kind} ({doc.get('file_name') or doc.get('file_id') or 'uploaded file'}) cannot be read. Please re-upload a clear {kind}.",
            })
    known = [(doc.get("file_id"), _patient_name(doc)) for doc in documents if _patient_name(doc)]
    if len({_normal_name(name) for _, name in known}) > 1:
        detail = ", ".join(f"{file_id or 'document'}: {name}" for file_id, name in known)
        corrections.append({
            "code": "PATIENT_MISMATCH",
            "patients": [{"file_id": file_id, "name": name} for file_id, name in known],
            "message": f"Documents show different patients ({detail}). Please upload documents for the same patient.",
        })
    trace.append({
        "stage": "document_gate", "rule_id": "document_requirements", "status": "FAIL" if corrections else "PASS",
        "policy_ref": f"document_requirements.{category}",
        "evidence": [{"file_id": doc.get("file_id"), "type": doc.get("doc_type") or doc.get("actual_type"), "quality": doc.get("quality", "GOOD"), "patient_name": _patient_name(doc), "provenance": doc.get("source")} for doc in documents],
        "details": corrections,
    })
    return corrections


def _pre_authorization(
    payload: dict[str, Any], policy: dict[str, Any], category_policy: dict[str, Any], category: str,
    items: list[dict[str, Any]], content: str, claimed: int, treatment_date: date | None,
) -> dict[str, Any]:
    """Match pre-authorization rules and classify the supplied approval evidence."""
    documents = payload.get("documents") or []
    config = policy["pre_authorization"]
    matched = []
    for rule in config["rules"]:
        terms = _term_hits(content, rule["terms"])
        if not terms:
            continue
        line_amount = sum(item["amount_paise"] for item in items if _term_hits(item["description"], rule["terms"]))
        basis = line_amount or claimed
        threshold = rule["amount_greater_than_paise"]
        if threshold is None or basis > threshold:
            matched.append({
                "rule_id": rule["id"], "label": rule["label"], "matched_terms": terms,
                "amount_basis": _rupees(basis),
                "amount_greater_than": None if threshold is None else _rupees(threshold),
                "source_paths": rule["source_paths"],
            })
    required = category_policy["requires_pre_auth"] or bool(matched)
    outcome: dict[str, Any] = {"required": required, "status": "NOT_REQUIRED", "code": None, "authorized_paise": None}
    if not required:
        outcome["trace"] = {"stage": "policy", "rule_id": "pre_authorization", "status": "PASS", "policy_ref": f"{category_policy['policy_ref']}.requires_pre_auth / pre_authorization.required_for", "details": "Not required for this evidence and amount."}
        return outcome

    pre_auth = payload.get("pre_authorization")
    form_status: bool | None = None
    if isinstance(pre_auth, bool):
        form_status = pre_auth
    elif isinstance(pre_auth, dict) and isinstance(pre_auth.get("obtained"), bool):
        form_status = pre_auth["obtained"]
    approval_document = next((doc for doc in documents if _doc_type(doc) == "PRE_AUTHORIZATION"), None)
    document_fields = _fields(approval_document) if approval_document else {}
    approval_reference = str(document_fields.get("approval_reference") or (pre_auth.get("approval_reference") if isinstance(pre_auth, dict) else "") or "").strip()
    issued_date = None
    raw_issued = document_fields.get("date") or (pre_auth.get("issued_date") if isinstance(pre_auth, dict) else None)
    if raw_issued:
        try:
            issued_date = _parse_document_date(raw_issued)
        except ValueError:
            issued_date = None
    raw_authorized = document_fields.get("approved_amount") or (pre_auth.get("approved_amount") if isinstance(pre_auth, dict) else None)
    authorized = _paise(raw_authorized) if raw_authorized not in (None, "") else None
    documented = approval_document is not None and issued_date is not None and bool(approval_reference)
    validity_days = config["validity_days"]

    code: str | None
    if form_status is False and documented:
        # The form and a dated approval record disagree; neither may silently win.
        status, code, source = "NOT_EVALUATED", "PRE_AUTH_CONFLICT", "form_document_conflict"
    elif documented:
        invalid = treatment_date is None or issued_date > treatment_date or (treatment_date - issued_date).days > validity_days  # type: ignore[operator]
        status, code, source = ("FAIL", "PRE_AUTH_INVALID", "document_backed_dated_approval") if invalid else ("PASS", None, "document_backed_dated_approval")
    elif form_status is True or approval_document is not None:
        status, code, source = "NOT_EVALUATED", "PRE_AUTH_STATUS_UNKNOWN", "claimed_without_dated_approval_record"
    else:
        # No approval record, reference, or claimed approval accompanies a claim
        # that the policy says needs one: pre-authorization was not obtained.
        status, code, source = "FAIL", "PRE_AUTH_MISSING", "no_approval_record_supplied"
    outcome.update(status=status, code=code, authorized_paise=authorized if status == "PASS" else None)
    outcome["trace"] = {
        "stage": "policy", "rule_id": "pre_authorization", "status": status,
        "policy_ref": "pre_authorization.required_for / opd_categories.*.high_value_tests_requiring_pre_auth / pre_authorization.validity_days",
        "evidence": {
            "matched_rules": matched, "category_requires_pre_auth": category_policy["requires_pre_auth"],
            "claimed_amount": _rupees(claimed), "form_status": form_status,
            "approval_document": (approval_document or {}).get("file_id"),
            "issued_date": issued_date.isoformat() if issued_date else None,
            "approval_reference": approval_reference or None,
            "authorized_amount": None if authorized is None else _rupees(authorized),
            "validity_days": validity_days, "status_source": source,
        },
    }
    labels = ", ".join(rule["label"] for rule in matched) or category
    outcome["message"] = {
        "PRE_AUTH_CONFLICT": "The form says pre-authorization was not obtained, but the uploaded dated approval record says it was. An operator must resolve the conflict before payment.",
        "PRE_AUTH_STATUS_UNKNOWN": "Pre-authorization is reported but no dated approval record with a reference was uploaded. Upload the insurer's approval record so it can be verified.",
        "PRE_AUTH_INVALID": f"The pre-authorization was issued after treatment or more than {validity_days} days before it.",
        "PRE_AUTH_MISSING": f"Pre-authorization is required for {labels} and no approval record was supplied. Obtain pre-authorization from the insurer, then resubmit the claim with the approval record (reference number and issue date).",
    }.get(code or "", "")
    return outcome


def _evaluate_claim(
    payload: dict[str, Any],
    policy: dict[str, Any],
    optional_risk_enricher: OptionalRiskEnricher,
) -> dict[str, Any]:
    """Apply document and policy rules to normalized evidence.

    Repairable document problems return decision=None. Rules that the
    evidence cannot support are made explicit as NOT_EVALUATED in the trace.
    """
    trace: list[dict[str, Any]] = [_policy_source_step(policy)]
    reasons: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    factors: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    category = str(payload.get("claim_category", "")).upper()
    documents = payload.get("documents") or []
    result: dict[str, Any] = {
        "state": "RECEIVED", "decision": None, "approved_amount": None,
        "approved_amount_paise": None, "reasons": reasons,
        "correction_requests": [], "confidence_score": CONFIDENCE_BASE,
        "trace": trace, "ledger": ledger,
    }

    def factor(code: str, points: float, applies_to: str, **evidence: Any) -> None:
        factors.append({"reason": code, "points": points, "applies_to": applies_to, **evidence})

    try:
        claimed = _paise(payload.get("claimed_amount"))
    except ValueError as exc:
        result.update(state="NEEDS_CORRECTION", confidence_score=0.0)
        result["correction_requests"] = [{"code": "INVALID_AMOUNT", "message": str(exc)}]
        trace.append({"stage": "intake", "rule_id": "claimed_amount", "status": "FAIL", "details": str(exc)})
        return result

    corrections = _document_gate(payload, policy, category, trace)
    if corrections:
        result.update(state="NEEDS_CORRECTION", correction_requests=corrections, confidence_score=0.9)
        return result

    weak_documents = [doc.get("file_id") for doc in documents if _text(doc.get("quality", "GOOD")) not in {"good", "clear", "readable"}]
    if weak_documents:
        factor("weak_document_quality", 0.10, "any", documents=weak_documents)
    document_names = [str(_patient_name(doc)) for doc in documents if _patient_name(doc)]
    if not document_names:
        # A missing name cannot contradict the submitting member, so it does not
        # block a decision; it is disclosed and matters only when money is paid.
        trace.append({"stage": "identity", "rule_id": "patient_identity", "status": "NOT_EVALUATED", "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."})
        factor("patient_name_unavailable", 0.04, "payable")
        advisories.append({"code": "PATIENT_IDENTITY_NOT_VERIFIED", "message": "No patient name was readable on the documents; payment is attributed to the submitting member and should be verified at settlement."})

    category_policy = policy["categories"].get(category)
    members = {member["member_id"]: member for member in policy["members"]}
    member = members.get(str(payload.get("member_id")))
    if payload.get("policy_id") != policy["policy_id"] or category_policy is None or member is None:
        reasons.append({"code": "MEMBERSHIP_OR_POLICY_UNKNOWN", "message": "Policy, member, or category could not be verified."})
        trace.append({"stage": "eligibility", "rule_id": "membership", "status": "UNKNOWN", "policy_ref": "members / policy_id / opd_categories"})
        result.update(state="MANUAL_REVIEW", decision="MANUAL_REVIEW", approved_amount=0, approved_amount_paise=0, confidence_score=0.4)
        return result
    category_ref = category_policy["policy_ref"]
    trace.append({"stage": "eligibility", "rule_id": "membership", "status": "PASS", "policy_ref": f"{member['policy_ref']} / policy_id / {category_ref}", "evidence": {"member_id": member["member_id"], "category": category}})

    owner = members.get(member.get("primary_member_id") or "", member)
    allowed_ids = {owner["member_id"], *owner["dependents"]}
    allowed_names = {_normal_name(members[member_id]["name"]) for member_id in allowed_ids if member_id in members}
    unexpected_names = [name for name in document_names if _normal_name(name) not in allowed_names]
    trace.append({"stage": "identity", "rule_id": "roster_patient_match", "status": "FAIL" if unexpected_names else "PASS" if document_names else "NOT_EVALUATED", "policy_ref": "members", "evidence": {"document_names": document_names, "allowed_names": sorted(allowed_names), "unresolved_roster_dependents": owner["unresolved_dependents"]}})
    if unexpected_names:
        correction = {"code": "PATIENT_NOT_COVERED", "message": f"The uploaded documents name {', '.join(unexpected_names)}, who is not listed as this member or a covered dependent. Please upload documents for a covered patient or correct the member ID."}
        result.update(state="NEEDS_CORRECTION", decision=None, approved_amount=None, approved_amount_paise=None, correction_requests=[correction])
        return result

    bill_documents = [doc for doc in documents if _doc_type(doc).endswith("BILL")]
    bill_totals = []
    line_totals = []
    for doc in bill_documents:
        fields = _fields(doc)
        if fields.get("total") is not None:
            bill_totals.append(_paise(fields["total"]))
        if fields.get("line_items"):
            line_totals.append(sum(_paise(item.get("amount")) for item in fields["line_items"]))
    bill_total = sum(bill_totals) if bill_totals else None
    line_total = sum(line_totals) if line_totals else None
    amount_conflict = (bill_total is not None and bill_total != claimed) or (line_total is not None and line_total != claimed) or (bill_total is not None and line_total is not None and bill_total != line_total)
    trace.append({"stage": "reconciliation", "rule_id": "bill_amount", "status": "FAIL" if amount_conflict else "NOT_EVALUATED" if bill_total is None and line_total is None else "PASS", "evidence": {"claimed_paise": claimed, "bill_total_paise": bill_total, "line_items_total_paise": line_total, "bill_files": [doc.get("file_id") for doc in bill_documents]}})
    if amount_conflict:
        correction = {"code": "AMOUNT_MISMATCH", "message": f"Claimed amount ₹{_rupees(claimed)} does not match the bill total or line items. Please correct the claimed amount or upload a matching itemized bill."}
        result.update(state="NEEDS_CORRECTION", decision=None, approved_amount=None, approved_amount_paise=None, correction_requests=[correction], confidence_score=0.75)
        return result
    if bill_total is None and line_total is None:
        factor("bill_amount_unavailable", 0.08, "any")

    items = _line_items(documents, claimed)
    content = _all_content(documents)
    clinical_content = " ".join(
        str(_fields(doc).get(field, ""))
        for doc in documents for field in ("diagnosis", "treatment", "test_name")
    )
    claim_exclusions = [entry for entry in policy["exclusions"] if "claim" in entry["applies_to"]]
    exclusion_hits = _exclusion_hits(clinical_content, claim_exclusions)
    definite_exclusions = [hit for hit in exclusion_hits if not hit["qualifier"]]
    qualified_exclusions = [hit for hit in exclusion_hits if hit["qualifier"]]
    trace.append({"stage": "policy", "rule_id": "excluded_condition", "status": "FAIL" if definite_exclusions else "FLAG" if qualified_exclusions else "PASS", "policy_ref": "exclusions.conditions", "evidence": exclusion_hits})
    if definite_exclusions:
        reasons.append({"code": "EXCLUDED_CONDITION", "message": f"Treatment is excluded under the policy: {', '.join(hit['label'] for hit in definite_exclusions)}."})
        if not any(hit["policy_text_match"] for hit in definite_exclusions):
            factor("exclusion_matched_by_interpretation_only", 0.06, "rejection", exclusions=[hit["exclusion_id"] for hit in definite_exclusions])
    if qualified_exclusions:
        reasons.append({"code": "EXCLUSION_QUALIFIER_REVIEW", "message": f"The documents mention {', '.join(hit['label'] for hit in qualified_exclusions)}; whether the policy qualifier applies must be confirmed by a reviewer."})

    treatment_date = None
    join_date = None
    try:
        treatment_date = date.fromisoformat(str(payload["treatment_date"]))
    except (KeyError, TypeError, ValueError):
        treatment_date = None
    if member.get("join_date"):
        join_date = date.fromisoformat(member["join_date"])
    if treatment_date is None:
        trace.append(_rule_trace(
            "treatment_date", "FAIL", "claim.treatment_date", {},
            "A treatment date is required before policy timing can be evaluated.",
        ))
        reasons.append({"code": "TREATMENT_DATE_REQUIRED", "message": "Provide the treatment date before this claim can be adjudicated."})
    document_dates: list[tuple[str, date]] = []
    unreadable_document_dates: list[str] = []
    for doc in documents:
        raw_date = _fields(doc).get("date")
        if not raw_date or _doc_type(doc) == "PRE_AUTHORIZATION":
            continue
        try:
            document_dates.append((str(doc.get("file_id")), _parse_document_date(raw_date)))
        except ValueError:
            unreadable_document_dates.append(str(doc.get("file_id")))
    if treatment_date and (document_dates or unreadable_document_dates):
        conflicts = [file_id for file_id, document_date in document_dates if document_date != treatment_date]
        date_status = "FAIL" if conflicts or unreadable_document_dates else "PASS"
        trace.append(_rule_trace(
            "document_treatment_date", date_status, "claim.treatment_date",
            {"treatment_date": treatment_date.isoformat(), "document_dates": [{"file_id": file_id, "date": document_date.isoformat()} for file_id, document_date in document_dates], "unreadable_date_files": unreadable_document_dates},
        ))
        if conflicts or unreadable_document_dates:
            reasons.append({"code": "DOCUMENT_DATE_CONFLICT", "message": "A document date does not match the submitted treatment date, or a document date could not be read. Review the episode before payment."})
    else:
        trace.append(_rule_trace("document_treatment_date", "NOT_EVALUATED", "claim.treatment_date", {}, "No comparable document date was extracted."))

    limits = policy["limits"]
    holder = policy["policy_holder"]
    renewal = holder["renewal_status"]
    trace.append(_rule_trace("policy_renewal_status", "PASS" if renewal == "ACTIVE" else "FAIL", "policy_holder.renewal_status", {"renewal_status": renewal}))
    if renewal != "ACTIVE":
        reasons.append({"code": "POLICY_NOT_ACTIVE", "message": f"Policy renewal status is {renewal}; automatic payment is not allowed."})
    floater = limits["family_floater"]
    relationship_ok = member["covered_relationship"] in floater["covered_relationships"]
    trace.append(_rule_trace(
        "covered_relationship", "PASS" if relationship_ok else "FAIL",
        "coverage.family_floater.covered_relationships", {
            "relationship": member["relationship"],
            "covered_relationship": member["covered_relationship"],
        },
    ))
    if not relationship_ok:
        reasons.append({"code": "RELATIONSHIP_NOT_COVERED", "message": f"Relationship {member['relationship']} is outside the covered family list."})
    sum_insured = limits["sum_insured_per_employee_paise"]
    sum_insured_used = payload.get("sum_insured_used")
    sum_insured_remaining = None if sum_insured_used is None else max(0, sum_insured - _paise(sum_insured_used))
    trace.append(_rule_trace("sum_insured", "NOT_EVALUATED" if sum_insured_remaining is None else "PASS" if sum_insured_remaining >= claimed else "LIMITED", "coverage.sum_insured_per_employee", {"sum_insured_paise": sum_insured, "used_paise": None if sum_insured_used is None else _paise(sum_insured_used), "remaining_paise": sum_insured_remaining}, "Aggregate limit; applied only when utilisation is supplied with the claim."))
    floater_limit = floater["combined_limit_paise"]
    floater_used = payload.get("family_floater_used")
    floater_remaining = None if floater_used is None else max(0, floater_limit - _paise(floater_used))
    trace.append(_rule_trace("family_floater_limit", "NOT_EVALUATED" if floater_remaining is None else "PASS" if floater_remaining >= claimed else "LIMITED", "coverage.family_floater.combined_limit", {"enabled": floater["enabled"], "combined_limit_paise": floater_limit, "used_paise": None if floater_used is None else _paise(floater_used), "remaining_paise": floater_remaining}, "Aggregate limit; applied only when family utilisation is supplied with the claim."))

    waiting_config = policy["waiting_periods"]
    pre_existing_days = waiting_config["pre_existing_days"]
    pre_existing_evidence = payload.get("pre_existing_conditions")
    pre_existing_names = [
        str(item.get("condition") if isinstance(item, dict) else item)
        for item in (pre_existing_evidence or [])
        if str(item.get("condition") if isinstance(item, dict) else item).strip()
    ] if isinstance(pre_existing_evidence, list) else []
    pre_existing_hits = [name for name in pre_existing_names if _contains_phrase(content, name)]
    if treatment_date and join_date and pre_existing_hits and pre_existing_days:
        eligible_from = join_date + timedelta(days=pre_existing_days)
        pre_existing_fail = treatment_date < eligible_from
        trace.append(_rule_trace(
            "pre_existing_condition_wait", "FAIL" if pre_existing_fail else "PASS",
            "waiting_periods.pre_existing_conditions_days",
            {"conditions": pre_existing_hits, "days": pre_existing_days, "join_date": str(join_date), "treatment_date": str(treatment_date), "eligible_from": str(eligible_from)},
        ))
        if pre_existing_fail:
            reasons.append({"code": "PRE_EXISTING_WAITING_PERIOD", "message": f"The pre-existing-condition waiting period ends on {eligible_from.isoformat()}; treatment was on {treatment_date.isoformat()}."})
    else:
        trace.append(_rule_trace("pre_existing_condition_wait", "NOT_EVALUATED", "waiting_periods.pre_existing_conditions_days", {"days": pre_existing_days, "conditions": pre_existing_names}, "No explicit pre-existing-condition evidence was supplied with the claim."))
    category_covered = category_policy["covered"]
    trace.append(_rule_trace("category_covered", "PASS" if category_covered else "FAIL", f"{category_ref}.covered", {"covered": category_covered}))
    if not category_covered:
        reasons.append({"code": "CATEGORY_NOT_COVERED", "message": f"{category} is not a covered OPD category under this policy."})

    if treatment_date and join_date:
        relevant = []
        for condition in waiting_config["specific_conditions"]:
            terms = _term_hits(content, condition["terms"])
            if terms:
                relevant.append((condition, terms))
        if relevant:
            condition, matched_terms = max(relevant, key=lambda item: item[0]["days"])
            condition_name, wait_days, wait_ref = condition["condition"], condition["days"], condition["policy_ref"]
        else:
            condition_name, wait_days, matched_terms = "initial", waiting_config["initial_days"], []
            wait_ref = "waiting_periods.initial_waiting_period_days"
        eligible_from = join_date + timedelta(days=wait_days)
        waiting_fail = treatment_date < eligible_from
        trace.append({"stage": "policy", "rule_id": "waiting_period", "status": "FAIL" if waiting_fail else "PASS", "policy_ref": wait_ref, "evidence": {"condition": condition_name, "matched_terms": matched_terms, "join_date": str(join_date), "join_date_source": member["join_date_source"], "treatment_date": str(treatment_date), "eligible_from": str(eligible_from)}})
        if waiting_fail:
            reasons.append({"code": "WAITING_PERIOD", "message": f"The {condition_name.replace('_', ' ')} waiting period ends on {eligible_from.isoformat()}; treatment was on {treatment_date.isoformat()}. Claims for this condition are eligible from {eligible_from.isoformat()}."})
    else:
        trace.append({"stage": "policy", "rule_id": "waiting_period", "status": "NOT_EVALUATED", "policy_ref": "waiting_periods", "details": "Treatment or join date unavailable."})
        if treatment_date and not join_date:
            reasons.append({"code": "MEMBER_START_DATE_UNKNOWN", "message": "The covered member's enrollment date is unavailable; waiting-period eligibility needs review."})

    if treatment_date:
        policy_start = date.fromisoformat(holder["policy_start_date"])
        policy_end = date.fromisoformat(holder["policy_end_date"])
        outside_policy = treatment_date < policy_start or treatment_date > policy_end
        trace.append(_rule_trace(
            "policy_coverage_period", "FAIL" if outside_policy else "PASS",
            "policy_holder.policy_start_date / policy_holder.policy_end_date",
            {"treatment_date": treatment_date.isoformat(), "policy_start_date": policy_start.isoformat(), "policy_end_date": policy_end.isoformat()},
        ))
        if outside_policy:
            reasons.append({"code": "OUTSIDE_POLICY_PERIOD", "message": f"Treatment date {treatment_date.isoformat()} is outside the policy period {policy_start.isoformat()} to {policy_end.isoformat()}."})

    submission_rules = policy["submission_rules"]
    minimum = submission_rules["minimum_claim_amount_paise"]
    below_minimum = claimed < minimum
    trace.append(_rule_trace("minimum_claim_amount", "FAIL" if below_minimum else "PASS", "submission_rules.minimum_claim_amount", {"claimed_amount_paise": claimed, "minimum_claim_amount_paise": minimum}))
    if below_minimum:
        reasons.append({"code": "MINIMUM_CLAIM_AMOUNT", "message": f"Claim amount is below the minimum of ₹{_rupees(minimum)}."})

    submission_date = payload.get("submission_date")
    deadline = submission_rules["deadline_days"]
    if treatment_date and submission_date:
        try:
            age = (date.fromisoformat(str(submission_date)) - treatment_date).days
            late = age > deadline
            before_treatment = age < 0
            trace.append({"stage": "policy", "rule_id": "submission_deadline", "status": "FAIL" if late or before_treatment else "PASS", "policy_ref": "submission_rules.deadline_days_from_treatment", "evidence": {"days_elapsed": age, "deadline_days": deadline}})
            if before_treatment:
                reasons.append({"code": "SUBMISSION_BEFORE_TREATMENT", "message": f"Submission date is {abs(age)} days before the treatment date."})
            elif late:
                reasons.append({"code": "SUBMISSION_LATE", "message": f"Claim was submitted {age} days after treatment; deadline is {deadline} days."})
        except ValueError:
            trace.append({"stage": "policy", "rule_id": "submission_deadline", "status": "NOT_EVALUATED", "details": "Submission date invalid."})
    else:
        trace.append({"stage": "policy", "rule_id": "submission_deadline", "status": "NOT_EVALUATED", "policy_ref": "submission_rules.deadline_days_from_treatment", "details": "Submission date absent."})

    pre_auth = _pre_authorization(payload, policy, category_policy, category, items, content, claimed, treatment_date)
    trace.append(pre_auth["trace"])
    if pre_auth["code"]:
        reasons.append({"code": pre_auth["code"], "message": pre_auth["message"]})

    fraud = policy["fraud_thresholds"]
    history = payload.get("claims_history") or []
    history_source = payload.get("claims_history_source", "claim_payload")
    same_day = sum(1 for item in history if item.get("date") == payload.get("treatment_date")) + 1
    same_day_limit = fraud["same_day_claims_limit"]
    fraud_flag = same_day > same_day_limit
    trace.append({"stage": "risk", "rule_id": "same_day_claims", "status": "FLAG" if fraud_flag else "PASS", "policy_ref": "fraud_thresholds.same_day_claims_limit", "evidence": {"same_day_claim_count_including_current": same_day, "limit": same_day_limit, "prior_claims": [{"claim_id": item.get("claim_id"), "date": item.get("date"), "amount": item.get("amount"), "provider": item.get("provider")} for item in history if item.get("date") == payload.get("treatment_date")], "history_source": history_source}})
    if fraud_flag:
        reasons.append({"code": "SAME_DAY_CLAIMS", "message": f"This is claim {same_day} on the same treatment date; policy review threshold is {same_day_limit}. Manual review is required."})

    month = str(payload.get("treatment_date", ""))[:7]
    monthly_count = sum(1 for item in history if str(item.get("date", ""))[:7] == month) + 1
    monthly_limit = fraud["monthly_claims_limit"]
    monthly_flag = bool(month and monthly_count > monthly_limit)
    trace.append({"stage": "risk", "rule_id": "monthly_claims", "status": "FLAG" if monthly_flag else "PASS", "policy_ref": "fraud_thresholds.monthly_claims_limit", "evidence": {"monthly_claim_count_including_current": monthly_count, "limit": monthly_limit, "month": month or None, "history_source": history_source}})
    if monthly_flag:
        reasons.append({"code": "MONTHLY_CLAIMS", "message": f"This is claim {monthly_count} in the treatment month; policy review threshold is {monthly_limit}. Manual review is required."})

    high_value_threshold = fraud["high_value_claim_threshold_paise"]
    auto_review_threshold = fraud["auto_manual_review_above_paise"]
    high_value_flag = claimed > high_value_threshold
    auto_review_flag = claimed > auto_review_threshold
    trace.append({"stage": "risk", "rule_id": "high_value_claim", "status": "FLAG" if high_value_flag else "PASS", "policy_ref": "fraud_thresholds.high_value_claim_threshold", "evidence": {"claimed_amount_paise": claimed, "threshold_paise": high_value_threshold}})
    trace.append({"stage": "risk", "rule_id": "auto_manual_review_amount", "status": "FLAG" if auto_review_flag else "PASS", "policy_ref": "fraud_thresholds.auto_manual_review_above", "evidence": {"claimed_amount_paise": claimed, "threshold_paise": auto_review_threshold}})
    if auto_review_flag:
        reasons.append({"code": "HIGH_VALUE_MANUAL_REVIEW", "message": f"Claimed amount ₹{_rupees(claimed)} exceeds the configured high-value manual-review threshold."})
    fraud_score = payload.get("fraud_score")
    fraud_score_threshold = Decimal(fraud["fraud_score_manual_review_threshold"])
    try:
        fraud_score_value = Decimal(str(fraud_score)) if fraud_score is not None else None
    except InvalidOperation:
        fraud_score_value = None
    fraud_score_flag = fraud_score_value is not None and fraud_score_value >= fraud_score_threshold
    trace.append({"stage": "risk", "rule_id": "fraud_score", "status": "NOT_EVALUATED" if fraud_score is None else "FLAG" if fraud_score_flag else "PASS", "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold", "evidence": {"score": float(fraud_score_value) if fraud_score_value is not None else None, "threshold": float(fraud_score_threshold)}})
    if fraud_score_flag:
        reasons.append({"code": "FRAUD_SCORE_REVIEW", "message": "The supplied fraud score meets the policy manual-review threshold."})

    try:
        optional_risk_enricher(payload)
    except Exception as exc:  # noqa: BLE001 - optional enrichment cannot block adjudication
        trace.append({"stage": "optional_risk_enrichment", "rule_id": "risk_enrichment", "status": "SKIPPED_COMPONENT_FAILURE", "degraded": True, "error_type": type(exc).__name__, "details": "Optional enrichment failed; mandatory document and policy checks completed."})
        reasons.append({"code": "COMPONENT_DEGRADED", "message": "Optional risk enrichment failed and was skipped; manual review is recommended because processing was incomplete."})
        factor("component_failure", 0.23, "any", component="risk_enrichment")
    else:
        trace.append({"stage": "optional_risk_enrichment", "rule_id": "risk_enrichment", "status": "PASS", "degraded": False})

    line_exclusions = [entry for entry in policy["exclusions"] if entry["scope"] in {"ALL", category}]
    allowlist = category_policy["covered_items"]
    brand_values = category_policy["brand_status_values"]
    eligible = 0
    eligible_before_limits = 0
    pharmacy_brand_unknown = False
    branded_items_paise = 0
    interpretation_only_lines = []
    for item in items:
        description = str(item["description"] or "").strip()
        hits = [hit for hit in _exclusion_hits(description, line_exclusions) if not hit["qualifier"]] if description else []
        excluded = bool(hits)
        covered_match = next((entry for entry in allowlist if _term_hits(description, entry["terms"])), None) if allowlist and description else None
        outside_allowlist = bool(allowlist) and bool(description) and not excluded and covered_match is None
        amount = item["amount_paise"]
        brand_status = item["brand_status"]
        if brand_status not in brand_values or not _contains_phrase(description, item["brand_evidence"]):
            brand_status = "UNKNOWN"
        if brand_values and not excluded:
            if brand_status == "UNKNOWN":
                pharmacy_brand_unknown = True
            elif brand_status == "BRANDED":
                branded_items_paise += amount
        item["brand_status"] = brand_status
        if excluded and not any(hit["policy_text_match"] for hit in hits):
            interpretation_only_lines.append(description)
        line_status = "EXCLUDED" if excluded else "UNKNOWN" if not description else "NOT_COVERED" if outside_allowlist else "ELIGIBLE"
        line_reason = "EXCLUDED_PROCEDURE" if excluded else "LINE_ITEM_DESCRIPTION_UNKNOWN" if not description else "NOT_ON_ALLOWLIST" if outside_allowlist else None
        line_ref = hits[0]["source_paths"][0] if excluded else category_policy["covered_items_ref"] if allowlist else f"{category_ref}.covered"
        ledger.append({"kind": "line_item", "description": item["description"], "source_document": item["source_document"], "amount_paise": amount, "amount": _rupees(amount), "status": line_status, "reason_code": line_reason, "reason": (f"Excluded under the policy: {hits[0]['label']}." if excluded else "Not on the covered list for this category." if outside_allowlist else "Line description is unreadable." if not description else "Covered."), "exclusion_matches": hits or None, "brand_status": brand_status if brand_values else None, "brand_evidence": item["brand_evidence"] if brand_values else None, "policy_ref": line_ref})
        if excluded:
            reasons.append({"code": "EXCLUDED_PROCEDURE", "message": f"{item['description']} is excluded ({hits[0]['label']}); ₹{_rupees(amount)} removed."})
        elif outside_allowlist:
            reasons.append({"code": "NOT_ON_ALLOWLIST", "message": f"{item['description']} is not on the covered list for {category}; ₹{_rupees(amount)} removed."})
        elif not description:
            reasons.append({"code": "LINE_ITEM_DESCRIPTION_UNKNOWN", "message": "A bill line has no readable description; its eligibility cannot be determined safely."})
        else:
            eligible += amount
            eligible_before_limits += amount
    if interpretation_only_lines:
        # Line exclusions determine the amount only on a payable (partial) outcome.
        factor("line_exclusion_matched_by_interpretation_only", 0.06, "payable", lines=interpretation_only_lines)

    # Per-claim ceiling: max(global per_claim_limit, category sub_limit), tested
    # against the eligible amount. A governing pre-authorization supersedes it.
    ceiling = category_policy["per_claim_ceiling_paise"]
    ceiling_evidence = {
        "claimed_amount": _rupees(claimed), "eligible_amount": _rupees(eligible), "limit": _rupees(ceiling),
        "global_per_claim_limit": _rupees(limits["per_claim_limit_paise"]), "category_sub_limit": _rupees(category_policy["sub_limit_paise"]),
        "limit_source": category_policy["per_claim_ceiling_ref"], "interpretation": "PER_CLAIM_CEILING_RULE",
    }
    over_ceiling = eligible > ceiling
    if not over_ceiling:
        ceiling_status = "PASS"
    elif pre_auth["required"] and pre_auth["status"] == "PASS" and pre_auth["authorized_paise"] is not None:
        ceiling_status = "AUTHORIZED_BY_PRE_AUTH"
    elif pre_auth["required"] and pre_auth["status"] == "PASS":
        ceiling_status = "NOT_EVALUATED"
        reasons.append({"code": "PRE_AUTH_AMOUNT_UNVERIFIED", "message": f"The eligible amount ₹{_rupees(eligible)} exceeds the ₹{_rupees(ceiling)} per-claim limit and the approval record states no authorized amount. Verify the pre-authorized amount before payment."})
    elif pre_auth["required"]:
        ceiling_status = "DEFERRED_TO_PRE_AUTH"
    else:
        ceiling_status = "FAIL"
        reasons.append({"code": "PER_CLAIM_EXCEEDED", "message": f"Claimed amount ₹{_rupees(claimed)} (eligible ₹{_rupees(eligible)}) exceeds the per-claim limit of ₹{_rupees(ceiling)} for {category.lower()} claims."})
    trace.append(_rule_trace("per_claim_limit", ceiling_status, category_policy["per_claim_ceiling_ref"], ceiling_evidence))
    benefit_limit_applied = False
    if ceiling_status == "AUTHORIZED_BY_PRE_AUTH" and eligible > pre_auth["authorized_paise"]:
        reduction = eligible - pre_auth["authorized_paise"]
        eligible -= reduction
        benefit_limit_applied = True
        ledger.append({"kind": "adjustment", "description": "Pre-authorized amount", "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": "pre_authorization"})

    annual_limit = limits["annual_opd_limit_paise"]
    ytd = payload.get("ytd_claims_amount")
    annual_remaining = None if ytd is None else max(0, annual_limit - _paise(ytd))
    trace.append({"stage": "policy", "rule_id": "annual_opd_limit", "status": "NOT_EVALUATED" if annual_remaining is None else "PASS" if annual_remaining >= eligible else "LIMITED", "policy_ref": "coverage.annual_opd_limit", "evidence": {"annual_limit": _rupees(annual_limit), "ytd_claims_amount": ytd, "ytd_source": payload.get("ytd_claims_source", "claim_payload" if ytd is not None else None), "remaining": None if annual_remaining is None else _rupees(annual_remaining)}, "details": None if annual_remaining is not None else "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."})
    if annual_remaining is None:
        factor("annual_opd_usage_not_evaluated", 0.04, "payable")
        advisories.append({"code": "ANNUAL_LIMIT_NOT_EVALUATED", "message": f"Year-to-date OPD usage was not supplied, so the ₹{_rupees(annual_limit)} annual OPD limit was not evaluated. Payment is subject to the member's remaining annual OPD balance."})
    elif eligible > annual_remaining:
        reduction = eligible - annual_remaining
        eligible = annual_remaining
        benefit_limit_applied = True
        reasons.append({"code": "ANNUAL_LIMIT_LIMITED", "message": f"The remaining annual OPD benefit limits this claim by ₹{_rupees(reduction)}."})
        ledger.append({"kind": "adjustment", "description": "Annual OPD remaining limit", "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": "coverage.annual_opd_limit"})

    present_types = {_doc_type(doc) for doc in documents}
    for advisory_document in category_policy["advisory_documents"]:
        missing = advisory_document not in present_types
        trace.append(_rule_trace(
            "advisory_document", "ADVISORY" if missing else "PASS", f"{category_ref}.requires_{advisory_document.lower()}",
            {"document": advisory_document, "present": not missing, "interpretation": f"DENTAL_REPORT_CONFLICT.{category}"},
            "The category flag and the document matrix disagree; the matrix (optional) governs, so absence does not block." if missing else None,
        ))
        if missing:
            factor("advisory_document_absent", 0.03, "payable", document=advisory_document)
            advisories.append({"code": "ADVISORY_DOCUMENT_ABSENT", "message": f"No {advisory_document} was uploaded. It is optional in the document requirements, so the claim was decided without it."})

    covered_system_unknown = False
    systems = category_policy["covered_systems"]
    if systems:
        system_text = " ".join([content, *(str(_fields(doc).get("doctor_name", "")) for doc in documents)])
        system_hits = [{"system": system["name"], "matched_terms": _term_hits(system_text, system["terms"])} for system in systems]
        system_hits = [hit for hit in system_hits if hit["matched_terms"]]
        covered_system_unknown = not system_hits
        trace.append(_rule_trace(
            "covered_system", "PASS" if system_hits else "NOT_EVALUATED", f"{category_ref}.covered_systems",
            {"matched": system_hits},
            None if system_hits else "No listed medical system was identified in the documents.",
        ))
        if covered_system_unknown:
            reasons.append({"code": "COVERED_SYSTEM_UNKNOWN", "message": "The documents do not establish a medical system covered by this policy. An operator must verify it before payment."})
    session_cap = category_policy["max_sessions_per_year"]
    if session_cap is not None:
        session_match = re.search(r"(\d+)\s+sessions?", content, flags=re.IGNORECASE)
        if session_match:
            sessions = int(session_match.group(1))
            prior_raw = payload.get("prior_sessions")
            prior_sessions = None if prior_raw is None else int(prior_raw)
            total_sessions = None if prior_sessions is None else sessions + prior_sessions
            over_sessions = sessions > session_cap or (total_sessions is not None and total_sessions > session_cap)
            session_status = "FAIL" if over_sessions else "NOT_EVALUATED" if prior_sessions is None else "PASS"
            trace.append(_rule_trace("max_sessions", session_status, f"{category_ref}.max_sessions_per_year", {"current_sessions": sessions, "prior_sessions": prior_sessions, "total_sessions": total_sessions, "max_sessions_per_year": session_cap, "history_source": payload.get("prior_sessions_source") if prior_sessions is not None else None}, "Prior session history was not supplied; only this claim's sessions were checked." if session_status == "NOT_EVALUATED" else None))
            if over_sessions:
                reasons.append({"code": "SESSION_LIMIT_EXCEEDED", "message": f"The claim would bring annual sessions to {total_sessions or sessions}; the annual cap is {session_cap}."})
            elif prior_sessions is None:
                factor("session_history_not_evaluated", 0.03, "payable")
                advisories.append({"code": "SESSION_HISTORY_NOT_EVALUATED", "message": f"Prior sessions this year were not supplied; this claim's {sessions} sessions are within the {session_cap}-session annual cap on their own."})
        else:
            trace.append(_rule_trace("max_sessions", "NOT_EVALUATED", f"{category_ref}.max_sessions_per_year", {"max_sessions_per_year": session_cap}, "Session count was not extracted."))
    if category_policy["requires_registered_practitioner"]:
        registrations = [str(_fields(doc).get("doctor_registration") or "").strip() for doc in documents]
        registered = [value for value in registrations if value]
        trace.append(_rule_trace(
            "registered_practitioner", "PASS" if registered else "NOT_EVALUATED",
            f"{category_ref}.requires_registered_practitioner", {"registrations": registered},
            None if registered else "Practitioner registration was not extracted.",
        ))
        if not registered:
            reasons.append({"code": "PRACTITIONER_REGISTRATION_UNKNOWN", "message": "The policy requires a registered practitioner, and registration was not extracted."})

    if sum_insured_remaining is not None and eligible > sum_insured_remaining:
        reduction = eligible - sum_insured_remaining
        eligible = sum_insured_remaining
        benefit_limit_applied = True
        reasons.append({"code": "SUM_INSURED_LIMITED", "message": f"The remaining sum insured limits this claim by ₹{_rupees(reduction)}."})
        ledger.append({"kind": "adjustment", "description": "Remaining sum insured limit", "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": "coverage.sum_insured_per_employee"})
    if floater["enabled"] and floater_remaining is not None and eligible > floater_remaining:
        reduction = eligible - floater_remaining
        eligible = floater_remaining
        benefit_limit_applied = True
        reasons.append({"code": "FAMILY_FLOATER_LIMITED", "message": f"The remaining family-floater benefit limits this claim by ₹{_rupees(reduction)}."})
        ledger.append({"kind": "adjustment", "description": "Family floater remaining limit", "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": "coverage.family_floater.combined_limit"})

    brand_scale = Decimal(0) if eligible_before_limits <= 0 else Decimal(eligible) / Decimal(eligible_before_limits)
    branded_eligible = int((Decimal(branded_items_paise) * brand_scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    hospital = str(payload.get("hospital_name") or "")
    if not hospital:
        hospital = next((str(_fields(doc).get("hospital_name")) for doc in documents if _fields(doc).get("hospital_name")), "")
    network_match = _network_match(hospital, policy) if hospital else None
    network_percent = category_policy["network_discount_percent"] if network_match else 0
    trace.append(_rule_trace("network_hospital", "PASS" if network_match else "NOT_EVALUATED" if not hospital else "NOT_APPLICABLE", "network_hospitals", {"hospital_name": hospital or None, "match": network_match, "match_rule": policy["network_match_rule"], "discount_percent": network_percent}))
    discount = _percentage(eligible, network_percent)
    after_discount = eligible - discount
    ledger.append({"kind": "adjustment", "description": "Network discount", "amount_paise": -discount, "amount": _rupees(-discount), "policy_ref": f"{category_ref}.network_discount_percent", "basis_paise": eligible, "percent": network_percent})
    copay_percent = category_policy["copay_percent"]
    if brand_values and pharmacy_brand_unknown:
        reasons.append({"code": "PHARMACY_BRAND_STATUS_UNKNOWN", "message": "The bill does not establish whether each medicine is branded or generic. Verify the product classification before applying pharmacy co-pay."})
    branded_lines = [item["description"] for item in items if item.get("brand_status") == "BRANDED"]
    generic_mandatory = category_policy["generic_mandatory"]
    trace.append(_rule_trace("generic_medicine_requirement", "FLAG" if generic_mandatory and branded_lines else "PASS", f"{category_ref}.generic_mandatory", {"generic_mandatory": generic_mandatory, "branded_lines": branded_lines}))
    if generic_mandatory and branded_lines:
        reasons.append({"code": "GENERIC_SUBSTITUTION_REVIEW", "message": "The policy requires generic medicines where available. A reviewer must verify the documented need for the branded medicine."})
    base_copay = _percentage(after_discount, copay_percent)
    brand_copay_percent = category_policy["branded_drug_copay_percent"]
    branded_after_discount = _percentage(branded_eligible, 100 - network_percent) if branded_eligible else 0
    branded_base_copay = _percentage(branded_after_discount, copay_percent) if branded_after_discount else 0
    branded_copay = _percentage(branded_after_discount, brand_copay_percent) if branded_after_discount and brand_copay_percent is not None else 0
    copay = base_copay - branded_base_copay + branded_copay
    payable = after_discount - copay
    ledger.append({"kind": "adjustment", "description": "Member co-pay", "amount_paise": -(base_copay - branded_base_copay), "amount": _rupees(-(base_copay - branded_base_copay)), "policy_ref": f"{category_ref}.copay_percent", "basis_paise": after_discount - branded_after_discount, "percent": copay_percent})
    if branded_copay:
        ledger.append({"kind": "adjustment", "description": "Branded medicine co-pay", "amount_paise": -branded_copay, "amount": _rupees(-branded_copay), "policy_ref": f"{category_ref}.branded_drug_copay_percent", "basis_paise": branded_after_discount, "percent": brand_copay_percent})
    trace.append({"stage": "pricing", "rule_id": "payable_amount", "status": "CALCULATED", "evidence": {"eligible_paise": eligible, "network_hospital": bool(network_match), "network_discount_paise": discount, "copay_paise": copay, "branded_basis_paise": branded_after_discount, "branded_copay_paise": branded_copay, "payable_paise": payable}, "details": "Network discount applied before co-pay."})

    codes = {reason["code"] for reason in reasons}
    primary = next((code for code in REJECT_PRIORITY if code in codes), None)
    review_codes = sorted(codes & REVIEW_CODES)
    if primary:
        decision, approved = "REJECTED", 0
    elif review_codes:
        decision, approved = "MANUAL_REVIEW", 0
    elif payable <= 0:
        decision, approved = "REJECTED", 0
        reasons.append({"code": "NO_PAYABLE_AMOUNT", "message": "No payable amount remains after policy adjustments."})
    elif payable < claimed and (benefit_limit_applied or any(item["status"] in {"EXCLUDED", "NOT_COVERED"} for item in ledger if item["kind"] == "line_item")):
        decision, approved = "PARTIAL", payable
    else:
        decision, approved = "APPROVED", payable
    if decision in PAYABLE:
        reasons.extend(advisories)
    if not reasons:
        reasons.append({"code": "COVERED", "message": "Claim passed the evaluated document and policy checks."})

    applicable = {"any"} | ({"payable"} if decision in PAYABLE else set()) | ({"rejection"} if decision == "REJECTED" else set())
    for item in factors:
        item["applied"] = item["applies_to"] in applicable
    score = round(max(0.0, CONFIDENCE_BASE - sum(item["points"] for item in factors if item["applied"])), 2)
    trace.append({"stage": "confidence", "rule_id": "confidence_rubric", "status": "DEGRADED" if score < CONFIDENCE_BASE else "PASS", "evidence": {"base": CONFIDENCE_BASE, "factors": factors, "score": score}, "details": CONFIDENCE_NOTE})
    trace.append({"stage": "decision", "rule_id": "outcome", "status": decision, "evidence": {"primary_reason": primary or (review_codes[0] if review_codes else None), "review_reasons": review_codes, "approved_amount_paise": approved}})
    result.update(state="DECIDED" if decision != "MANUAL_REVIEW" else "MANUAL_REVIEW", decision=decision, approved_amount=_rupees(approved), approved_amount_paise=approved, confidence_score=score)
    return result


def evaluate_claim(
    payload: dict[str, Any],
    policy: dict[str, Any],
    *,
    optional_risk_enricher: OptionalRiskEnricher = _optional_risk_enrichment,
) -> dict[str, Any]:
    """Evaluate one claim, routing malformed evidence or policy to review with a trace."""
    try:
        canonical = ensure_canonical(policy)
    except PolicyConfigurationError as exc:
        return {
            "state": "MANUAL_REVIEW", "decision": "MANUAL_REVIEW",
            "approved_amount": 0, "approved_amount_paise": 0,
            "reasons": [{"code": PolicyConfigurationError.code, "message": "The policy configuration failed validation; no claim can be adjudicated until it is corrected."}],
            "correction_requests": [], "confidence_score": 0.0,
            "trace": [{"stage": "configuration", "rule_id": "policy_schema", "status": "FAIL", "error_type": type(exc).__name__, "details": str(exc)[:500]}],
            "ledger": [],
        }
    try:
        return _evaluate_claim(payload, canonical, optional_risk_enricher)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return {
            "state": "MANUAL_REVIEW", "decision": "MANUAL_REVIEW",
            "approved_amount": 0, "approved_amount_paise": 0,
            "reasons": [{"code": "MALFORMED_EVIDENCE", "message": "Claim evidence could not be validated; manual review is required."}],
            "correction_requests": [], "confidence_score": 0.0,
            "trace": [{"stage": "validation", "rule_id": "input_schema", "status": "FAIL", "error_type": type(exc).__name__, "details": "Malformed input prevented safe adjudication."}],
            "ledger": [],
        }
