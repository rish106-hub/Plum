"""Pure, deterministic OPD claim evaluation.

All money is converted to integer paise before arithmetic. The result keeps a
complete ordered trace, including rules that cannot be evaluated from evidence.

The engine reads only the canonical policy configuration produced by
``claims.policy``; it never reads the raw policy JSON. Evidence provenance
(fixture, PDF text, OCR, model-assisted extraction) is recorded in the trace but
never changes which rule applies or how it is decided.

Evaluation runs as a fixed sequence of named stages over one ``_Claim`` context:

    intake -> document gate -> eligibility -> identity -> bill reconciliation
    -> dates -> coverage -> waiting periods -> claim exclusions -> pre-authorization
    -> risk thresholds -> risk-signal enrichment -> line items -> category rules
    -> per-claim ceiling -> pricing -> benefit limits -> decision and confidence

The first five stages may stop early (correction or review); every later stage
only records trace steps, reasons, advisories and confidence factors, and the
decision stage turns them into one outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable

from claims.money import to_paise as _paise
from claims.money import to_rupees as _rupees
from claims.policy import PolicyConfigurationError, audit_fingerprint, ensure_canonical

RiskEnricher = Callable[[dict[str, Any]], Any]
OptionalRiskEnricher = RiskEnricher  # backwards-compatible name

CONFIDENCE_BASE = 0.96
CONFIDENCE_NOTE = (
    "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by "
    "unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it "
    "could change that outcome or its amount."
)
REJECT_PRIORITY = [
    "POLICY_NOT_ACTIVE", "RELATIONSHIP_NOT_COVERED", "CATEGORY_NOT_COVERED", "OUTSIDE_POLICY_PERIOD",
    "MINIMUM_CLAIM_AMOUNT", "EXCLUDED_CONDITION", "PRE_EXISTING_WAITING_PERIOD", "WAITING_PERIOD",
    "SESSION_LIMIT_EXCEEDED", "PRE_AUTH_MISSING", "PRE_AUTH_INVALID", "SUBMISSION_BEFORE_TREATMENT",
    "SUBMISSION_LATE", "PER_CLAIM_EXCEEDED",
]
REVIEW_CODES = {
    "SAME_DAY_CLAIMS", "MONTHLY_CLAIMS", "HIGH_VALUE_MANUAL_REVIEW", "FRAUD_SCORE_REVIEW", "RISK_SIGNAL_REVIEW",
    "LINE_ITEM_DESCRIPTION_UNKNOWN", "LINE_ITEM_UNRESOLVED", "PHARMACY_BRAND_STATUS_UNKNOWN",
    "GENERIC_SUBSTITUTION_REVIEW", "PRE_AUTH_STATUS_UNKNOWN", "PRE_AUTH_CONFLICT", "PRE_AUTH_AMOUNT_UNVERIFIED",
    "COVERED_SYSTEM_UNKNOWN", "MEMBER_START_DATE_UNKNOWN", "TREATMENT_DATE_REQUIRED", "DOCUMENT_DATE_CONFLICT",
    "PRACTITIONER_REGISTRATION_UNKNOWN", "EXCLUSION_QUALIFIER_REVIEW", "BILL_AMOUNT_UNVERIFIED",
    "DOCUMENT_QUALITY_INSUFFICIENT", "PATIENT_IDENTITY_UNVERIFIED", "CATEGORY_SUB_LIMIT_UNVERIFIED",
}
SUB_LIMIT_USAGE_KEYS = (
    ("category_sub_limit_used", "exact: prior service benefit counted against this sub_limit"),
    ("category_ytd_claims_amount", "upper bound: all prior net benefit paid to this member in this category"),
)
"""Prior-usage inputs for the category sub_limit, most precise first."""
PAYABLE = {"APPROVED", "PARTIAL"}
GOOD_QUALITY = {"good", "clear", "readable"}
UNREADABLE_QUALITY = {"unreadable", "blurry", "illegible"}
IDENTITY_STATUSES = {"VERIFIED", "NOT_AVAILABLE", "UNVERIFIED", "FAILED"}
BLOCKING_IDENTITY_STATUSES = {"UNVERIFIED", "FAILED"}
UNITEMIZED_LINE = "Bill total (not itemized)"

# Rejections whose outcome could change if the patient's identity were different
# (a different family member has different enrolment, relationship or history).
IDENTITY_DEPENDENT_REJECTIONS = {
    "WAITING_PERIOD", "PRE_EXISTING_WAITING_PERIOD", "RELATIONSHIP_NOT_COVERED", "SESSION_LIMIT_EXCEEDED",
}
# Rejections decided by the treatment date.
DATE_DEPENDENT_REJECTIONS = {
    "WAITING_PERIOD", "PRE_EXISTING_WAITING_PERIOD", "OUTSIDE_POLICY_PERIOD", "SUBMISSION_LATE",
    "SUBMISSION_BEFORE_TREATMENT",
}
# Rejections decided by the bill amount.
AMOUNT_DEPENDENT_REJECTIONS = {"PER_CLAIM_EXCEEDED", "MINIMUM_CLAIM_AMOUNT", "PRE_AUTH_MISSING", "NO_PAYABLE_AMOUNT"}
PAYABLE_OR_REVIEW = ("payable", "review")


class InvalidClaimInput(ValueError):
    """A claim input field is present but invalid; the claim cannot be adjudicated safely."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field_name = field_name


# --------------------------------------------------------------------------
# Text and value helpers
# --------------------------------------------------------------------------


def _percentage(paise: int, percent: Any) -> int:
    return int((Decimal(paise) * Decimal(str(percent)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


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


def _term_hits(text: str, terms: list[dict[str, Any]], *, affirmative: bool = False) -> list[dict[str, Any]]:
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


def _is_bill(document: dict[str, Any]) -> bool:
    return _doc_type(document).endswith("BILL")


def _optional_paise(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return _paise(value)
    except ValueError as exc:
        raise InvalidClaimInput(key, f"{key} must be a non-negative amount") from exc


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


def claim_provider(payload: dict[str, Any]) -> str:
    hospital = str(payload.get("hospital_name") or "")
    if hospital:
        return hospital
    return next(
        (str(_fields(doc).get("hospital_name")) for doc in payload.get("documents") or [] if _fields(doc).get("hospital_name")),
        "",
    )


# --------------------------------------------------------------------------
# Risk-signal enrichment (a real, default-on component whose failure is tolerated)
# --------------------------------------------------------------------------


def risk_signal_enrichment(request: dict[str, Any]) -> dict[str, Any]:
    """Compute supplementary fraud signals from the member family's claim history.

    These signals are not policy rules. They complement the mandatory policy
    thresholds (same-day, monthly, auto-review amount, fraud score), which are
    evaluated separately and do not depend on this component:

    - ``trailing_30_day_value``: this claim plus the family's claims in the 30
      days up to the treatment date, against ``high_value_claim_threshold``.
    - ``repeat_billing``: a prior claim from the same provider for the same
      amount on the same treatment date (a second bill for one visit). Repeat
      visits on other days are ordinary follow-ups, and a re-submitted bill is
      caught earlier by the duplicate-bill check.

    A signal whose history fields are absent is NOT_EVALUATED. A FLAG routes the
    claim to review. If this component fails, adjudication continues without
    it, confidence is lowered and the failure is disclosed.
    """
    claim = request["claim"]
    thresholds = request["fraud_thresholds"]
    claimed = int(request["claimed_paise"])
    history = claim.get("claims_history") or []
    signals: list[dict[str, Any]] = []
    try:
        treatment = date.fromisoformat(str(claim.get("treatment_date")))
    except ValueError:
        treatment = None
    if treatment is None:
        return {"component": "risk_signal_enrichment", "version": 1, "signals": [
            {"signal": name, "status": "NOT_EVALUATED", "details": "Treatment date unavailable."}
            for name in ("trailing_30_day_value", "repeat_billing")
        ]}
    window_start = treatment - timedelta(days=30)
    window = []
    for item in history:
        try:
            item_date = date.fromisoformat(str(item.get("date")))
        except ValueError:
            continue
        if window_start <= item_date <= treatment:
            window.append(item)
    threshold = int(thresholds["high_value_claim_threshold_paise"])
    if all(item.get("amount") is not None for item in window):
        trailing = claimed + sum(_paise(item["amount"]) for item in window)
        # A single claim above the threshold is already routed by the policy's
        # auto-review threshold; this signal is about accumulation across claims.
        signals.append({
            "signal": "trailing_30_day_value", "status": "FLAG" if window and trailing > threshold else "PASS",
            "evidence": {"claims_in_window": len(window) + 1, "value_paise": trailing, "threshold_paise": threshold,
                         "window": [window_start.isoformat(), treatment.isoformat()]},
            "policy_ref": "fraud_thresholds.high_value_claim_threshold",
        })
    else:
        signals.append({"signal": "trailing_30_day_value", "status": "NOT_EVALUATED",
                        "details": "Claim history does not carry amounts.", "policy_ref": "fraud_thresholds.high_value_claim_threshold"})
    provider = _normal_words(claim_provider(claim))
    if provider and all(item.get("provider") and item.get("amount") is not None for item in window):
        repeats = [
            item.get("claim_id") for item in window
            if _normal_words(item["provider"]) == provider and _paise(item["amount"]) == claimed
            and str(item.get("date")) == treatment.isoformat()
        ]
        signals.append({"signal": "repeat_billing", "status": "FLAG" if repeats else "PASS",
                        "evidence": {"matching_claims": repeats, "provider": provider, "amount_paise": claimed}})
    else:
        signals.append({"signal": "repeat_billing", "status": "NOT_EVALUATED",
                        "details": "Provider or amount unavailable for this claim or its history."})
    return {"component": "risk_signal_enrichment", "version": 1, "signals": signals}


def _valid_enrichment(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or not isinstance(result.get("signals"), list):
        raise TypeError("risk enrichment returned no signal list")
    for signal in result["signals"]:
        if not isinstance(signal, dict) or not isinstance(signal.get("signal"), str) or signal.get("status") not in {"PASS", "FLAG", "NOT_EVALUATED"}:
            raise TypeError("risk enrichment returned a malformed signal")
    return result["signals"]


# --------------------------------------------------------------------------
# Evaluation context
# --------------------------------------------------------------------------


@dataclass
class _Claim:
    payload: dict[str, Any]
    policy: dict[str, Any]
    enricher: RiskEnricher
    trace: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[dict[str, Any]] = field(default_factory=list)
    advisories: list[dict[str, Any]] = field(default_factory=list)
    limit_reasons: list[dict[str, Any]] = field(default_factory=list)
    factors: list[dict[str, Any]] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    category: str = ""
    documents: list[dict[str, Any]] = field(default_factory=list)
    claimed: int = 0
    category_policy: dict[str, Any] = field(default_factory=dict)
    member: dict[str, Any] = field(default_factory=dict)
    document_names: list[str] = field(default_factory=list)
    treatment_date: date | None = None
    join_date: date | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    content: str = ""
    clinical_content: str = ""
    claim_exclusions: list[dict[str, Any]] = field(default_factory=list)
    pre_auth: dict[str, Any] = field(default_factory=dict)
    ceiling_status: str = "PASS"
    eligible: int = 0
    eligible_before_limits: int = 0
    branded_items_paise: int = 0
    pharmacy_brand_unknown: bool = False
    network_percent: int = 0
    payable: int = 0
    benefit_limit_applied: bool = False
    component_degraded: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)

    def reason(self, code: str, message: str) -> None:
        self.reasons.append({"code": code, "message": message})

    def advise(self, code: str, message: str) -> None:
        self.advisories.append({"code": code, "message": message})

    def factor(self, code: str, points: float, applies_to: tuple[str, ...], **evidence: Any) -> None:
        self.factors.append({"reason": code, "points": points, "applies_to": list(applies_to), **evidence})

    def result(self, **values: Any) -> dict[str, Any]:
        outcome: dict[str, Any] = {
            "state": "RECEIVED", "decision": None, "approved_amount": None,
            "approved_amount_paise": None, "reasons": self.reasons,
            "correction_requests": [], "confidence_score": CONFIDENCE_BASE,
            "trace": self.trace, "ledger": self.ledger,
        }
        outcome.update(values)
        return outcome

    def correction(self, request: dict[str, Any], confidence: float | None = None) -> dict[str, Any]:
        values: dict[str, Any] = {"state": "NEEDS_CORRECTION", "correction_requests": [request]}
        if confidence is not None:
            values["confidence_score"] = confidence
        return self.result(**values)


# --------------------------------------------------------------------------
# Stages that may stop evaluation early
# --------------------------------------------------------------------------


def _policy_source_step(policy: dict[str, Any]) -> dict[str, Any]:
    audit = policy.get("audit", [])
    return {
        "stage": "configuration", "rule_id": "policy_source", "status": "LOADED", "policy_ref": "policy",
        "evidence": {
            "policy_id": policy["policy_id"],
            "schema_version": policy["schema_version"],
            "source_sha256": policy["source"]["sha256"],
            "canonical_sha256": policy["canonical_sha256"],
            "canonical_sha256_verified": True,
            "audit_sha256": audit_fingerprint(audit),
            "audit_entries": len(audit),
            "interpretation_entries": sum(1 for entry in audit if entry["kind"] == "interpretation"),
            "conflict_resolutions": [entry["id"] for entry in audit if entry["kind"] == "conflict_resolution"],
            "audit": audit,
        },
        "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit "
                   "trail that produced it is recorded here so it is persisted with the decision.",
    }


def _stage_intake(claim: _Claim) -> dict[str, Any] | None:
    """Parse the claimed amount and validate optional numeric and history inputs."""
    payload = claim.payload
    try:
        claim.claimed = _paise(payload.get("claimed_amount"))
    except ValueError as exc:
        claim.trace.append({"stage": "intake", "rule_id": "claimed_amount", "status": "FAIL", "details": str(exc)})
        return claim.correction({"code": "INVALID_AMOUNT", "message": str(exc)}, confidence=0.0)
    currency = payload.get("currency")
    expected_currency = claim.policy["submission_rules"]["currency"]
    if currency is not None and str(currency).strip().upper() != expected_currency:
        raise InvalidClaimInput("currency", f"claims must be in {expected_currency}")
    fraud_score = payload.get("fraud_score")
    score: Decimal | None = None
    if fraud_score is not None:
        if isinstance(fraud_score, bool):
            raise InvalidClaimInput("fraud_score", "fraud_score must be a number between 0 and 1")
        try:
            score = Decimal(str(fraud_score).strip())
        except (InvalidOperation, ValueError) as exc:
            raise InvalidClaimInput("fraud_score", "fraud_score must be a number between 0 and 1") from exc
        if not score.is_finite() or not Decimal(0) <= score <= Decimal(1):
            raise InvalidClaimInput("fraud_score", "fraud_score must be a number between 0 and 1")
    prior_sessions = payload.get("prior_sessions")
    if prior_sessions is not None:
        if isinstance(prior_sessions, bool) or not re.fullmatch(r"\d+", str(prior_sessions).strip()):
            raise InvalidClaimInput("prior_sessions", "prior_sessions must be a non-negative whole number")
        prior_sessions = int(str(prior_sessions).strip())
    history = payload.get("claims_history")
    if history is not None and (not isinstance(history, list) or not all(isinstance(item, dict) for item in history)):
        raise InvalidClaimInput("claims_history", "claims_history must be a list of claim records")
    identity = payload.get("identity_verification")
    if identity is not None and str(identity).upper() not in IDENTITY_STATUSES:
        raise InvalidClaimInput("identity_verification", f"identity_verification must be one of {sorted(IDENTITY_STATUSES)}")
    claim.inputs = {
        "fraud_score": score,
        "prior_sessions": prior_sessions,
        "claims_history": history or [],
        "identity_verification": None if identity is None else str(identity).upper(),
        "ytd": _optional_paise(payload, "ytd_claims_amount"),
        "sum_insured_used": _optional_paise(payload, "sum_insured_used"),
        "family_floater_used": _optional_paise(payload, "family_floater_used"),
        **{key: _optional_paise(payload, key) for key, _ in SUB_LIMIT_USAGE_KEYS},
    }
    return None


def _stage_document_gate(claim: _Claim) -> dict[str, Any] | None:
    """Required document types, unreadable files and cross-document patient consistency."""
    documents = claim.documents
    requirements = claim.policy["document_requirements"].get(claim.category, {})
    present = {_doc_type(doc) for doc in documents}
    corrections: list[dict[str, Any]] = []
    for required_type in requirements.get("required", []):
        if required_type not in present:
            uploaded = ", ".join(sorted(present)) or "no documents"
            corrections.append({
                "code": "DOCUMENT_MISSING",
                "required_type": required_type,
                "uploaded_types": sorted(present),
                "message": f"Uploaded {uploaded}; please upload a readable {required_type} for this {claim.category.lower()} claim.",
            })
    for doc in documents:
        if _text(doc.get("quality", "GOOD")) in UNREADABLE_QUALITY:
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
    claim.trace.append({
        "stage": "document_gate", "rule_id": "document_requirements", "status": "FAIL" if corrections else "PASS",
        "policy_ref": f"document_requirements.{claim.category}",
        "evidence": [{"file_id": doc.get("file_id"), "type": doc.get("doc_type") or doc.get("actual_type"), "quality": doc.get("quality", "GOOD"), "patient_name": _patient_name(doc), "provenance": doc.get("source")} for doc in documents],
        "details": corrections,
    })
    if corrections:
        return claim.result(state="NEEDS_CORRECTION", correction_requests=corrections, confidence_score=0.9)
    return None


def _stage_eligibility(claim: _Claim) -> dict[str, Any] | None:
    """Policy, member and category must all be known."""
    members = {member["member_id"]: member for member in claim.policy["members"]}
    member = members.get(str(claim.payload.get("member_id")))
    category_policy = claim.policy["categories"].get(claim.category)
    if claim.payload.get("policy_id") != claim.policy["policy_id"] or category_policy is None or member is None:
        claim.reason("MEMBERSHIP_OR_POLICY_UNKNOWN", "Policy, member, or category could not be verified.")
        claim.trace.append({"stage": "eligibility", "rule_id": "membership", "status": "UNKNOWN", "policy_ref": "members / policy_id / opd_categories"})
        return claim.result(state="MANUAL_REVIEW", decision="MANUAL_REVIEW", approved_amount=0, approved_amount_paise=0, confidence_score=0.4)
    claim.member = member
    claim.category_policy = category_policy
    claim.trace.append({"stage": "eligibility", "rule_id": "membership", "status": "PASS", "policy_ref": f"{member['policy_ref']} / policy_id / {category_policy['policy_ref']}", "evidence": {"member_id": member["member_id"], "category": claim.category}})
    return None


def _stage_identity(claim: _Claim) -> dict[str, Any] | None:
    """Patient names on the documents against the member's covered roster and the document layer's verdict."""
    documents = claim.documents
    claim.document_names = [str(_patient_name(doc)) for doc in documents if _patient_name(doc)]
    if not claim.document_names:
        # A missing name cannot contradict the submitting member, so it does not
        # block a decision; it is disclosed and lowers confidence where material.
        claim.trace.append({"stage": "identity", "rule_id": "patient_identity", "status": "NOT_EVALUATED", "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."})
        claim.factor("patient_name_unavailable", 0.04, (*PAYABLE_OR_REVIEW, "identity_dependent_rejection"))
        claim.advise("PATIENT_IDENTITY_NOT_VERIFIED", "No patient name was readable on the documents; payment is attributed to the submitting member and should be verified at settlement.")
    members = {member["member_id"]: member for member in claim.policy["members"]}
    owner = members.get(claim.member.get("primary_member_id") or "", claim.member)
    allowed_ids = {owner["member_id"], *owner["dependents"]}
    allowed_names = {_normal_name(members[member_id]["name"]) for member_id in allowed_ids if member_id in members}
    unexpected = [name for name in claim.document_names if _normal_name(name) not in allowed_names]
    claim.trace.append({"stage": "identity", "rule_id": "roster_patient_match", "status": "FAIL" if unexpected else "PASS" if claim.document_names else "NOT_EVALUATED", "policy_ref": "members", "evidence": {"document_names": claim.document_names, "allowed_names": sorted(allowed_names), "unresolved_roster_dependents": owner["unresolved_dependents"]}})
    if unexpected:
        return claim.correction({"code": "PATIENT_NOT_COVERED", "message": f"The uploaded documents name {', '.join(unexpected)}, who is not listed as this member or a covered dependent. Please upload documents for a covered patient or correct the member ID."})
    status = claim.inputs["identity_verification"]
    claim.trace.append({"stage": "identity", "rule_id": "identity_verification", "status": "NOT_EVALUATED" if status in (None, "NOT_AVAILABLE") else "PASS" if status == "VERIFIED" else "FAIL", "evidence": {"document_layer_status": status}, "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."})
    if status in BLOCKING_IDENTITY_STATUSES:
        claim.reason("PATIENT_IDENTITY_UNVERIFIED", "The document layer could not verify that the documents belong to a covered patient. An operator must confirm the patient before payment.")
    return None


def _stage_bill_reconciliation(claim: _Claim) -> dict[str, Any] | None:
    """Claimed amount against bill totals and line items; document quality for payment."""
    bills = [doc for doc in claim.documents if _is_bill(doc)]
    bill_totals, line_totals = [], []
    for doc in bills:
        fields = _fields(doc)
        if fields.get("total") is not None:
            bill_totals.append(_paise(fields["total"]))
        if fields.get("line_items"):
            line_totals.append(sum(_paise(item.get("amount")) for item in fields["line_items"]))
    bill_total = sum(bill_totals) if bill_totals else None
    line_total = sum(line_totals) if line_totals else None
    conflict = (
        (bill_total is not None and bill_total != claim.claimed)
        or (line_total is not None and line_total != claim.claimed)
        or (bill_total is not None and line_total is not None and bill_total != line_total)
    )
    established = bill_total is not None or line_total is not None
    claim.trace.append({"stage": "reconciliation", "rule_id": "bill_amount", "status": "FAIL" if conflict else "PASS" if established else "NOT_EVALUATED", "evidence": {"claimed_paise": claim.claimed, "bill_total_paise": bill_total, "line_items_total_paise": line_total, "bill_files": [doc.get("file_id") for doc in bills]}})
    if conflict:
        return claim.correction({"code": "AMOUNT_MISMATCH", "message": f"Claimed amount ₹{_rupees(claim.claimed)} does not match the bill total or line items. Please correct the claimed amount or upload a matching itemized bill."}, confidence=0.75)
    if not established:
        claim.factor("bill_amount_unavailable", 0.08, (*PAYABLE_OR_REVIEW, "amount_dependent_rejection"))
        claim.reason("BILL_AMOUNT_UNVERIFIED", "No bill total or itemized amount was established from the bill, so no amount can be paid. An operator must verify the bill.")

    required = set(claim.policy["document_requirements"].get(claim.category, {}).get("required", []))
    weak = [doc for doc in claim.documents if _text(doc.get("quality", "GOOD")) not in GOOD_QUALITY]
    weak_required = [doc.get("file_id") for doc in weak if _doc_type(doc) in required]
    claim.trace.append({"stage": "reconciliation", "rule_id": "document_quality", "status": "FAIL" if weak_required else "FLAG" if weak else "PASS", "evidence": {"weak_documents": [{"file_id": doc.get("file_id"), "type": _doc_type(doc), "quality": doc.get("quality")} for doc in weak], "required_types": sorted(required)}})
    if weak:
        claim.factor("weak_document_quality", 0.10, ("payable", "review", "rejection"), documents=[doc.get("file_id") for doc in weak])
    if weak_required:
        claim.reason("DOCUMENT_QUALITY_INSUFFICIENT", "A required document was only partially readable, so its facts are not reliable enough for payment. An operator must verify it.")
    claim.items = _line_items(claim.documents)
    claim.content = _all_content(claim.documents)
    claim.clinical_content = " ".join(
        str(_fields(doc).get(name, "")) for doc in claim.documents for name in ("diagnosis", "treatment", "test_name")
    )
    return None


def _line_items(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bill lines as printed; a bill with a total but no lines contributes one unitemized line of that total."""
    lines: list[dict[str, Any]] = []
    for document in documents:
        if not _is_bill(document):
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
                    "itemized": True,
                }
                for item in fields["line_items"]
            )
        elif fields.get("total") is not None:
            lines.append({
                "description": UNITEMIZED_LINE, "amount_paise": _paise(fields["total"]), "brand_status": "UNKNOWN",
                "brand_evidence": "", "source_document": document.get("file_id"), "itemized": False,
            })
    return lines


def _all_content(documents: list[dict[str, Any]]) -> str:
    parts = []
    for document in documents:
        fields = _fields(document)
        for name in ("diagnosis", "treatment", "test_name"):
            parts.append(str(fields.get(name, "")))
        parts.extend(str(test) for test in fields.get("tests_ordered", []))
        parts.extend(str(item.get("description", "")) for item in fields.get("line_items", []))
    return " ".join(parts).casefold()


# --------------------------------------------------------------------------
# Policy stages
# --------------------------------------------------------------------------


def _stage_dates(claim: _Claim) -> None:
    """The submitted treatment date and its corroboration by document dates."""
    try:
        claim.treatment_date = date.fromisoformat(str(claim.payload["treatment_date"]))
    except (KeyError, TypeError, ValueError):
        claim.treatment_date = None
    if claim.member.get("join_date"):
        claim.join_date = date.fromisoformat(claim.member["join_date"])
    if claim.treatment_date is None:
        claim.trace.append(_rule_trace("treatment_date", "FAIL", "claim.treatment_date", {}, "A treatment date is required before policy timing can be evaluated."))
        claim.reason("TREATMENT_DATE_REQUIRED", "Provide the treatment date before this claim can be adjudicated.")
    document_dates: list[tuple[str, date]] = []
    unreadable: list[str] = []
    for doc in claim.documents:
        raw_date = _fields(doc).get("date")
        if not raw_date or _doc_type(doc) == "PRE_AUTHORIZATION":
            continue
        try:
            document_dates.append((str(doc.get("file_id")), _parse_document_date(raw_date)))
        except ValueError:
            unreadable.append(str(doc.get("file_id")))
    if claim.treatment_date and (document_dates or unreadable):
        conflicts = [file_id for file_id, value in document_dates if value != claim.treatment_date]
        failed = bool(conflicts or unreadable)
        claim.trace.append(_rule_trace(
            "document_treatment_date", "FAIL" if failed else "PASS", "claim.treatment_date",
            {"treatment_date": claim.treatment_date.isoformat(), "document_dates": [{"file_id": file_id, "date": value.isoformat()} for file_id, value in document_dates], "unreadable_date_files": unreadable},
        ))
        if failed:
            claim.reason("DOCUMENT_DATE_CONFLICT", "A document date does not match the submitted treatment date, or a document date could not be read. Review the episode before payment.")
    else:
        claim.trace.append(_rule_trace("document_treatment_date", "NOT_EVALUATED", "claim.treatment_date", {}, "No comparable document date was extracted."))
        if claim.treatment_date:
            claim.factor("treatment_date_not_corroborated", 0.03, ("payable", "date_dependent_rejection"))
            claim.advise("TREATMENT_DATE_NOT_CORROBORATED", "No document carries a readable date, so the timing rules used the submitted treatment date without corroboration.")


def _stage_coverage(claim: _Claim) -> None:
    """Renewal status, covered relationship, category, policy period, minimum amount and submission deadline."""
    policy, holder = claim.policy, claim.policy["policy_holder"]
    renewal = holder["renewal_status"]
    claim.trace.append(_rule_trace("policy_renewal_status", "PASS" if renewal == "ACTIVE" else "FAIL", "policy_holder.renewal_status", {"renewal_status": renewal}))
    if renewal != "ACTIVE":
        claim.reason("POLICY_NOT_ACTIVE", f"Policy renewal status is {renewal}; automatic payment is not allowed.")
    floater = policy["limits"]["family_floater"]
    relationship_ok = claim.member["covered_relationship"] in floater["covered_relationships"]
    claim.trace.append(_rule_trace("covered_relationship", "PASS" if relationship_ok else "FAIL", "coverage.family_floater.covered_relationships", {"relationship": claim.member["relationship"], "covered_relationship": claim.member["covered_relationship"]}))
    if not relationship_ok:
        claim.reason("RELATIONSHIP_NOT_COVERED", f"Relationship {claim.member['relationship']} is outside the covered family list.")
    category_ref = claim.category_policy["policy_ref"]
    covered = claim.category_policy["covered"]
    claim.trace.append(_rule_trace("category_covered", "PASS" if covered else "FAIL", f"{category_ref}.covered", {"covered": covered}))
    if not covered:
        claim.reason("CATEGORY_NOT_COVERED", f"{claim.category} is not a covered OPD category under this policy.")
    treatment_date = claim.treatment_date
    if treatment_date:
        start = date.fromisoformat(holder["policy_start_date"])
        end = date.fromisoformat(holder["policy_end_date"])
        outside = treatment_date < start or treatment_date > end
        claim.trace.append(_rule_trace("policy_coverage_period", "FAIL" if outside else "PASS", "policy_holder.policy_start_date / policy_holder.policy_end_date", {"treatment_date": treatment_date.isoformat(), "policy_start_date": start.isoformat(), "policy_end_date": end.isoformat()}))
        if outside:
            claim.reason("OUTSIDE_POLICY_PERIOD", f"Treatment date {treatment_date.isoformat()} is outside the policy period {start.isoformat()} to {end.isoformat()}.")
    rules = policy["submission_rules"]
    minimum = rules["minimum_claim_amount_paise"]
    below = claim.claimed < minimum
    claim.trace.append(_rule_trace("minimum_claim_amount", "FAIL" if below else "PASS", "submission_rules.minimum_claim_amount", {"claimed_amount_paise": claim.claimed, "minimum_claim_amount_paise": minimum}))
    if below:
        claim.reason("MINIMUM_CLAIM_AMOUNT", f"Claim amount is below the minimum of ₹{_rupees(minimum)}.")
    submission_date = claim.payload.get("submission_date")
    deadline = rules["deadline_days"]
    if treatment_date and submission_date:
        try:
            age = (date.fromisoformat(str(submission_date)) - treatment_date).days
        except ValueError:
            claim.trace.append({"stage": "policy", "rule_id": "submission_deadline", "status": "NOT_EVALUATED", "details": "Submission date invalid."})
            return
        late, early = age > deadline, age < 0
        claim.trace.append({"stage": "policy", "rule_id": "submission_deadline", "status": "FAIL" if late or early else "PASS", "policy_ref": "submission_rules.deadline_days_from_treatment", "evidence": {"days_elapsed": age, "deadline_days": deadline}})
        if early:
            claim.reason("SUBMISSION_BEFORE_TREATMENT", f"Submission date is {abs(age)} days before the treatment date.")
        elif late:
            claim.reason("SUBMISSION_LATE", f"Claim was submitted {age} days after treatment; deadline is {deadline} days.")
    else:
        claim.trace.append({"stage": "policy", "rule_id": "submission_deadline", "status": "NOT_EVALUATED", "policy_ref": "submission_rules.deadline_days_from_treatment", "details": "Submission date absent."})


def _stage_waiting_periods(claim: _Claim) -> None:
    """Pre-existing-condition and condition-specific waiting periods, ignoring negated mentions."""
    config = claim.policy["waiting_periods"]
    treatment_date, join_date = claim.treatment_date, claim.join_date
    pre_existing_days = config["pre_existing_days"]
    evidence = claim.payload.get("pre_existing_conditions")
    names = [
        str(item.get("condition") if isinstance(item, dict) else item)
        for item in (evidence or [])
        if str(item.get("condition") if isinstance(item, dict) else item).strip()
    ] if isinstance(evidence, list) else []
    hits = [name for name in names if _affirmative(claim.content, name)]
    if treatment_date and join_date and hits and pre_existing_days:
        eligible_from = join_date + timedelta(days=pre_existing_days)
        failed = treatment_date < eligible_from
        claim.trace.append(_rule_trace("pre_existing_condition_wait", "FAIL" if failed else "PASS", "waiting_periods.pre_existing_conditions_days", {"conditions": hits, "days": pre_existing_days, "join_date": str(join_date), "treatment_date": str(treatment_date), "eligible_from": str(eligible_from)}))
        if failed:
            claim.reason("PRE_EXISTING_WAITING_PERIOD", f"The pre-existing-condition waiting period ends on {eligible_from.isoformat()}; treatment was on {treatment_date.isoformat()}.")
    else:
        claim.trace.append(_rule_trace("pre_existing_condition_wait", "NOT_EVALUATED", "waiting_periods.pre_existing_conditions_days", {"days": pre_existing_days, "conditions": names}, "No explicit pre-existing-condition evidence was supplied with the claim."))

    if not (treatment_date and join_date):
        claim.trace.append({"stage": "policy", "rule_id": "waiting_period", "status": "NOT_EVALUATED", "policy_ref": "waiting_periods", "details": "Treatment or join date unavailable."})
        if treatment_date and not join_date:
            claim.reason("MEMBER_START_DATE_UNKNOWN", "The covered member's enrollment date is unavailable; waiting-period eligibility needs review.")
        return
    relevant = []
    for condition in config["specific_conditions"]:
        terms = _term_hits(claim.content, condition["terms"], affirmative=True)
        if terms:
            relevant.append((condition, terms))
    if relevant:
        condition, matched_terms = max(relevant, key=lambda item: item[0]["days"])
        name, days, ref = condition["condition"], condition["days"], condition["policy_ref"]
    else:
        name, days, matched_terms, ref = "initial", config["initial_days"], [], "waiting_periods.initial_waiting_period_days"
    eligible_from = join_date + timedelta(days=days)
    failed = treatment_date < eligible_from
    claim.trace.append({"stage": "policy", "rule_id": "waiting_period", "status": "FAIL" if failed else "PASS", "policy_ref": ref, "evidence": {"condition": name, "matched_terms": matched_terms, "join_date": str(join_date), "join_date_source": claim.member["join_date_source"], "treatment_date": str(treatment_date), "eligible_from": str(eligible_from)}})
    if failed:
        claim.reason("WAITING_PERIOD", f"The {name.replace('_', ' ')} waiting period ends on {eligible_from.isoformat()}; treatment was on {treatment_date.isoformat()}. Claims for this condition are eligible from {eligible_from.isoformat()}.")


def _stage_claim_exclusions(claim: _Claim) -> None:
    """Whole-claim exclusions matched in diagnosis, treatment and test names."""
    exclusions = [entry for entry in claim.policy["exclusions"] if "claim" in entry["applies_to"]]
    hits = _exclusion_hits(claim.clinical_content, exclusions)
    definite = [hit for hit in hits if not hit["qualifier"]]
    qualified = [hit for hit in hits if hit["qualifier"]]
    claim.claim_exclusions = definite
    claim.trace.append({"stage": "policy", "rule_id": "excluded_condition", "status": "FAIL" if definite else "FLAG" if qualified else "PASS", "policy_ref": "exclusions.conditions", "evidence": hits})
    if definite:
        claim.reason("EXCLUDED_CONDITION", f"Treatment is excluded under the policy: {', '.join(hit['label'] for hit in definite)}.")
        if not any(hit["policy_text_match"] for hit in definite):
            claim.factor("exclusion_matched_by_interpretation_only", 0.06, ("rejection",), exclusions=[hit["exclusion_id"] for hit in definite])
    if qualified:
        claim.reason("EXCLUSION_QUALIFIER_REVIEW", f"The documents mention {', '.join(hit['label'] for hit in qualified)}; whether the policy qualifier applies must be confirmed by a reviewer.")


def _service_texts(documents: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(texts where any pre-auth term may match, test/line entries where short forms may match)."""
    test_or_line: list[str] = []
    service: list[str] = []
    for document in documents:
        fields = _fields(document)
        if fields.get("treatment"):
            service.append(str(fields["treatment"]))
        if fields.get("test_name"):
            test_or_line.append(str(fields["test_name"]))
        test_or_line.extend(str(test) for test in fields.get("tests_ordered", []) or [])
        test_or_line.extend(str(item.get("description", "")) for item in fields.get("line_items", []) or [])
    return service + test_or_line, test_or_line


def _pre_auth_term_hits(term_owner: dict[str, Any], texts: list[str], test_or_line: list[str]) -> list[dict[str, Any]]:
    hits = []
    for term in term_owner["terms"]:
        if term.get("context") == "test_or_line":
            candidates = test_or_line
            needed = term.get("requires_any") or []
            matched = any(
                _affirmative(entry, term["text"]) and (not needed or any(_contains_phrase(entry, word) for word in needed))
                for entry in candidates
            )
        else:
            matched = any(_affirmative(entry, term["text"]) for entry in texts)
        if matched:
            hits.append(term)
    return hits


def _stage_pre_authorization(claim: _Claim) -> None:
    claim.pre_auth = _pre_authorization(claim)
    claim.trace.append(claim.pre_auth["trace"])
    if claim.pre_auth["code"]:
        claim.reason(claim.pre_auth["code"], claim.pre_auth["message"])
    if claim.pre_auth["status"] == "PASS":
        claim.factor("pre_auth_record_member_supplied", 0.05, ("payable",))
        claim.advise("PRE_AUTH_NOT_VERIFIED_WITH_INSURER", "The pre-authorization approval record was uploaded by the member and has not been confirmed with the insurer; confirm it before settlement.")


def _pre_authorization(claim: _Claim) -> dict[str, Any]:
    """Match pre-authorization rules and classify the supplied approval evidence."""
    payload, category_policy = claim.payload, claim.category_policy
    documents = claim.documents
    config = claim.policy["pre_authorization"]
    texts, test_or_line = _service_texts(documents)
    matched = []
    for rule in config["rules"]:
        terms = _pre_auth_term_hits(rule, texts, test_or_line)
        if not terms:
            continue
        line_amount = sum(item["amount_paise"] for item in claim.items if item["itemized"] and _pre_auth_term_hits(rule, [item["description"]], [item["description"]]))
        basis = line_amount or claim.claimed
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
        outcome["trace"] = {"stage": "policy", "rule_id": "pre_authorization", "status": "PASS", "policy_ref": f"{category_policy['policy_ref']}.requires_pre_auth / pre_authorization.required_for", "details": "Not required for the services and amount in this claim."}
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
    treatment_date = claim.treatment_date

    code: str | None
    if form_status is False and documented:
        # The form and a dated approval record disagree; neither may silently win.
        status, code, source = "NOT_EVALUATED", "PRE_AUTH_CONFLICT", "form_document_conflict"
    elif documented and issued_date is not None:
        invalid = treatment_date is None or issued_date > treatment_date or (treatment_date - issued_date).days > validity_days
        # The record is member-uploaded; the engine cannot confirm it with the insurer.
        status, code = ("FAIL", "PRE_AUTH_INVALID") if invalid else ("PASS", None)
        source = "member_supplied_record_unverified_with_insurer"
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
            "claimed_amount": _rupees(claim.claimed), "form_status": form_status,
            "approval_document": (approval_document or {}).get("file_id"),
            "issued_date": issued_date.isoformat() if issued_date else None,
            "approval_reference": approval_reference or None,
            "authorized_amount": None if authorized is None else _rupees(authorized),
            "validity_days": validity_days, "status_source": source,
            "insurer_verified": False,
        },
    }
    labels = ", ".join(rule["label"] for rule in matched) or claim.category
    outcome["message"] = {
        "PRE_AUTH_CONFLICT": "The form says pre-authorization was not obtained, but the uploaded dated approval record says it was. An operator must resolve the conflict before payment.",
        "PRE_AUTH_STATUS_UNKNOWN": "Pre-authorization is reported but no dated approval record with a reference was uploaded. Upload the insurer's approval record so it can be verified.",
        "PRE_AUTH_INVALID": f"The pre-authorization was issued after treatment or more than {validity_days} days before it.",
        "PRE_AUTH_MISSING": f"Pre-authorization is required for {labels} and no approval record was supplied. Obtain pre-authorization from the insurer, then resubmit the claim with the approval record (reference number and issue date).",
    }.get(code or "", "")
    return outcome


def _stage_risk_thresholds(claim: _Claim) -> None:
    """The policy's own fraud thresholds; these are mandatory and never skipped."""
    payload, fraud = claim.payload, claim.policy["fraud_thresholds"]
    history = claim.inputs["claims_history"]
    history_source = payload.get("claims_history_source", "claim_payload")
    treatment = payload.get("treatment_date")
    same_day = sum(1 for item in history if item.get("date") == treatment) + 1
    same_day_limit = fraud["same_day_claims_limit"]
    same_day_flag = same_day > same_day_limit
    claim.trace.append({"stage": "risk", "rule_id": "same_day_claims", "status": "FLAG" if same_day_flag else "PASS", "policy_ref": "fraud_thresholds.same_day_claims_limit", "evidence": {"same_day_claim_count_including_current": same_day, "limit": same_day_limit, "prior_claims": [{"claim_id": item.get("claim_id"), "date": item.get("date"), "amount": item.get("amount"), "provider": item.get("provider")} for item in history if item.get("date") == treatment], "history_source": history_source}})
    if same_day_flag:
        claim.reason("SAME_DAY_CLAIMS", f"This is claim {same_day} on the same treatment date; policy review threshold is {same_day_limit}. Manual review is required.")
    month = str(treatment or "")[:7]
    monthly = sum(1 for item in history if str(item.get("date", ""))[:7] == month) + 1
    monthly_limit = fraud["monthly_claims_limit"]
    monthly_flag = bool(month and monthly > monthly_limit)
    claim.trace.append({"stage": "risk", "rule_id": "monthly_claims", "status": "FLAG" if monthly_flag else "PASS", "policy_ref": "fraud_thresholds.monthly_claims_limit", "evidence": {"monthly_claim_count_including_current": monthly, "limit": monthly_limit, "month": month or None, "history_source": history_source}})
    if monthly_flag:
        claim.reason("MONTHLY_CLAIMS", f"This is claim {monthly} in the treatment month; policy review threshold is {monthly_limit}. Manual review is required.")
    high_value = fraud["high_value_claim_threshold_paise"]
    auto_review = fraud["auto_manual_review_above_paise"]
    claim.trace.append({"stage": "risk", "rule_id": "high_value_claim", "status": "FLAG" if claim.claimed > high_value else "PASS", "policy_ref": "fraud_thresholds.high_value_claim_threshold", "evidence": {"claimed_amount_paise": claim.claimed, "threshold_paise": high_value}, "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."})
    auto_flag = claim.claimed > auto_review
    claim.trace.append({"stage": "risk", "rule_id": "auto_manual_review_amount", "status": "FLAG" if auto_flag else "PASS", "policy_ref": "fraud_thresholds.auto_manual_review_above", "evidence": {"claimed_amount_paise": claim.claimed, "threshold_paise": auto_review}})
    if auto_flag:
        claim.reason("HIGH_VALUE_MANUAL_REVIEW", f"Claimed amount ₹{_rupees(claim.claimed)} exceeds the configured high-value manual-review threshold.")
    score = claim.inputs["fraud_score"]
    threshold = Decimal(fraud["fraud_score_manual_review_threshold"])
    score_flag = score is not None and score >= threshold
    claim.trace.append({"stage": "risk", "rule_id": "fraud_score", "status": "NOT_EVALUATED" if score is None else "FLAG" if score_flag else "PASS", "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold", "evidence": {"score": None if score is None else float(score), "threshold": float(threshold)}})
    if score_flag:
        claim.reason("FRAUD_SCORE_REVIEW", "The supplied fraud score meets the policy manual-review threshold.")


def _stage_risk_enrichment(claim: _Claim) -> None:
    """Run the risk-signal enrichment component; its failure degrades, never blocks, adjudication."""
    request = {"claim": claim.payload, "fraud_thresholds": claim.policy["fraud_thresholds"], "claimed_paise": claim.claimed}
    try:
        signals = _valid_enrichment(claim.enricher(request))
    except Exception as exc:  # noqa: BLE001 - enrichment failure must not block adjudication
        claim.component_degraded = True
        claim.trace.append({"stage": "optional_risk_enrichment", "rule_id": "risk_enrichment", "status": "SKIPPED_COMPONENT_FAILURE", "degraded": True, "error_type": type(exc).__name__, "details": "The risk-signal enrichment component failed and was skipped; mandatory document, policy and fraud-threshold checks completed."})
        claim.reason("COMPONENT_DEGRADED", "The risk-signal enrichment component (30-day claim value and repeat-billing signals) failed and was skipped. All mandatory document, policy and fraud-threshold checks completed and the decision rests on them, but processing was incomplete, so a manual review of this decision is recommended.")
        claim.factor("component_failure", 0.23, ("payable", "review", "rejection"), component="risk_enrichment")
        return
    flagged = [signal for signal in signals if signal["status"] == "FLAG"]
    claim.trace.append({"stage": "optional_risk_enrichment", "rule_id": "risk_enrichment", "status": "FLAG" if flagged else "PASS", "degraded": False, "evidence": signals})
    if flagged:
        claim.reason("RISK_SIGNAL_REVIEW", f"Risk signals need review: {', '.join(signal['signal'] for signal in flagged)}.")


def _classify_line(claim: _Claim, item: dict[str, Any], line_exclusions: list[dict[str, Any]]) -> dict[str, Any]:
    """One ledger line: EXCLUDED, NOT_COVERED, UNRESOLVED, UNKNOWN or ELIGIBLE, with its reason."""
    category_policy = claim.category_policy
    category_ref = category_policy["policy_ref"]
    description = str(item["description"] or "").strip()
    amount = item["amount_paise"]
    hits = _exclusion_hits(description, line_exclusions) if description and item["itemized"] else []
    definite = [hit for hit in hits if not hit["qualifier"]]
    qualified = [hit for hit in hits if hit["qualifier"]]
    allowlist = category_policy["covered_items"]
    covered = next((entry for entry in allowlist if _term_hits(description, entry["terms"])), None) if allowlist and item["itemized"] else None
    other_category = None
    if allowlist and covered is None and item["itemized"] and not definite:
        other_category = next(
            (name for name, other in claim.policy["categories"].items()
             if name != claim.category and any(_term_hits(description, entry["terms"]) for entry in other["covered_items"])),
            None,
        )
    status: str
    if not description:
        status, code, text = "UNKNOWN", "LINE_ITEM_DESCRIPTION_UNKNOWN", "Line description is unreadable."
    elif definite:
        status, code, text = "EXCLUDED", "EXCLUDED_PROCEDURE", f"Excluded under the policy: {definite[0]['label']}."
    elif qualified:
        status, code, text = "UNRESOLVED", "EXCLUSION_QUALIFIER_REVIEW", f"Matches '{qualified[0]['label']}'; whether its qualifier applies must be confirmed."
    elif allowlist and not item["itemized"]:
        status, code, text = "UNRESOLVED", "LINE_ITEM_UNRESOLVED", f"The bill is not itemized, so it cannot be checked against the {claim.category.lower()} covered list."
    elif other_category:
        status, code, text = "NOT_COVERED", "NOT_ON_ALLOWLIST", f"This is a {other_category.lower()} item, not on the {claim.category.lower()} covered list."
    elif allowlist and covered is None:
        status, code, text = "UNRESOLVED", "LINE_ITEM_UNRESOLVED", f"Not recognised as a covered or an excluded {claim.category.lower()} item; a reviewer must classify it."
    else:
        status, code, text = "ELIGIBLE", None, "Covered."
    ref = (
        definite[0]["source_paths"][0] if definite else qualified[0]["source_paths"][0] if qualified
        else category_policy["covered_items_ref"] if allowlist else f"{category_ref}.covered"
    )
    brand_values = category_policy["brand_status_values"]
    brand_status = item["brand_status"]
    if brand_status not in brand_values or not _contains_phrase(description, item["brand_evidence"]):
        brand_status = "UNKNOWN"
    item["brand_status"] = brand_status
    return {
        "kind": "line_item", "description": item["description"], "source_document": item["source_document"],
        "amount_paise": amount, "amount": _rupees(amount), "status": status, "reason_code": code, "reason": text,
        "itemized": item["itemized"], "exclusion_matches": hits or None,
        "covered_item": covered["label"] if covered else None,
        "brand_status": brand_status if brand_values else None,
        "brand_evidence": item["brand_evidence"] if brand_values else None, "policy_ref": ref,
    }


def _stage_line_items(claim: _Claim) -> None:
    """Classify each bill line against exclusions and the category's covered list."""
    line_exclusions = [entry for entry in claim.policy["exclusions"] if entry["scope"] in {"ALL", claim.category}]
    brand_values = claim.category_policy["brand_status_values"]
    interpretation_only = []
    for item in claim.items:
        line = _classify_line(claim, item, line_exclusions)
        claim.ledger.append(line)
        amount, status = line["amount_paise"], line["status"]
        if brand_values and status != "EXCLUDED":
            if line["brand_status"] == "UNKNOWN":
                claim.pharmacy_brand_unknown = True
            elif line["brand_status"] == "BRANDED":
                claim.branded_items_paise += amount
        if status == "EXCLUDED":
            hits = [hit for hit in line["exclusion_matches"] if not hit["qualifier"]]
            if not any(hit["policy_text_match"] for hit in hits):
                interpretation_only.append(line["description"])
            claim.reason("EXCLUDED_PROCEDURE", f"{line['description']} is excluded ({hits[0]['label']}); ₹{line['amount']} removed.")
        elif status == "NOT_COVERED":
            claim.reason("NOT_ON_ALLOWLIST", f"{line['description']} is not on the covered list for {claim.category}; ₹{line['amount']} removed.")
        elif status == "UNRESOLVED":
            claim.reason(line["reason_code"], f"{line['description']} (₹{line['amount']}): {line['reason']}")
        elif status == "UNKNOWN":
            claim.reason("LINE_ITEM_DESCRIPTION_UNKNOWN", "A bill line has no readable description; its eligibility cannot be determined safely.")
        else:
            claim.eligible += amount
    claim.eligible_before_limits = claim.eligible
    if interpretation_only:
        # Line exclusions determine the amount only on a payable (partial) outcome.
        claim.factor("line_exclusion_matched_by_interpretation_only", 0.06, ("payable",), lines=interpretation_only)


def _stage_category_rules(claim: _Claim) -> None:
    """Advisory documents, covered medical systems, annual sessions and practitioner registration."""
    category_policy, documents = claim.category_policy, claim.documents
    category_ref = category_policy["policy_ref"]
    present = {_doc_type(doc) for doc in documents}
    for advisory_document in category_policy["advisory_documents"]:
        missing = advisory_document not in present
        claim.trace.append(_rule_trace(
            "advisory_document", "ADVISORY" if missing else "PASS", f"{category_ref}.requires_{advisory_document.lower()}",
            {"document": advisory_document, "present": not missing, "interpretation": f"DENTAL_REPORT_CONFLICT.{claim.category}"},
            "The category flag and the document matrix disagree; the matrix (optional) governs, so absence does not block." if missing else None,
        ))
        if missing:
            claim.factor("advisory_document_absent", 0.03, ("payable",), document=advisory_document)
            claim.advise("ADVISORY_DOCUMENT_ABSENT", f"No {advisory_document} was uploaded. It is optional in the document requirements, so the claim was decided without it.")
    systems = category_policy["covered_systems"]
    if systems:
        system_text = " ".join([claim.content, *(str(_fields(doc).get("doctor_name", "")) for doc in documents)])
        system_hits = [{"system": system["name"], "matched_terms": _term_hits(system_text, system["terms"])} for system in systems]
        system_hits = [hit for hit in system_hits if hit["matched_terms"]]
        claim.trace.append(_rule_trace("covered_system", "PASS" if system_hits else "NOT_EVALUATED", f"{category_ref}.covered_systems", {"matched": system_hits}, None if system_hits else "No listed medical system was identified in the documents."))
        if not system_hits:
            claim.reason("COVERED_SYSTEM_UNKNOWN", "The documents do not establish a medical system covered by this policy. An operator must verify it before payment.")
    cap = category_policy["max_sessions_per_year"]
    if cap is not None:
        match = re.search(r"(\d+)\s+sessions?", claim.content, flags=re.IGNORECASE)
        if match:
            sessions = int(match.group(1))
            prior = claim.inputs["prior_sessions"]
            total = None if prior is None else sessions + prior
            over = sessions > cap or (total is not None and total > cap)
            status = "FAIL" if over else "NOT_EVALUATED" if prior is None else "PASS"
            claim.trace.append(_rule_trace("max_sessions", status, f"{category_ref}.max_sessions_per_year", {"current_sessions": sessions, "prior_sessions": prior, "total_sessions": total, "max_sessions_per_year": cap, "history_source": claim.payload.get("prior_sessions_source") if prior is not None else None}, "Prior session history was not supplied; only this claim's sessions were checked." if status == "NOT_EVALUATED" else None))
            if over:
                claim.reason("SESSION_LIMIT_EXCEEDED", f"The claim would bring annual sessions to {total or sessions}; the annual cap is {cap}.")
            elif prior is None:
                claim.factor("session_history_not_evaluated", 0.03, ("payable",))
                claim.advise("SESSION_HISTORY_NOT_EVALUATED", f"Prior sessions this year were not supplied; this claim's {sessions} sessions are within the {cap}-session annual cap on their own.")
        else:
            claim.trace.append(_rule_trace("max_sessions", "NOT_EVALUATED", f"{category_ref}.max_sessions_per_year", {"max_sessions_per_year": cap}, "Session count was not extracted."))
    if category_policy["requires_registered_practitioner"]:
        registered = [value for value in (str(_fields(doc).get("doctor_registration") or "").strip() for doc in documents) if value]
        claim.trace.append(_rule_trace("registered_practitioner", "PASS" if registered else "NOT_EVALUATED", f"{category_ref}.requires_registered_practitioner", {"registrations": registered}, None if registered else "Practitioner registration was not extracted."))
        if not registered:
            claim.reason("PRACTITIONER_REGISTRATION_UNKNOWN", "The policy requires a registered practitioner, and registration was not extracted.")


def _stage_per_claim_ceiling(claim: _Claim) -> None:
    """max(global per_claim_limit, category sub_limit) on the eligible amount; a governing pre-auth decides instead."""
    category_policy, pre_auth = claim.category_policy, claim.pre_auth
    ceiling = category_policy["per_claim_ceiling_paise"]
    eligible = claim.eligible
    evidence = {
        "claimed_amount": _rupees(claim.claimed), "eligible_amount": _rupees(eligible), "limit": _rupees(ceiling),
        "global_per_claim_limit": _rupees(claim.policy["limits"]["per_claim_limit_paise"]),
        "category_sub_limit": _rupees(category_policy["sub_limit_paise"]),
        "limit_source": category_policy["per_claim_ceiling_ref"], "interpretation": "PER_CLAIM_CEILING_RULE",
    }
    if eligible <= ceiling:
        status = "PASS"
    elif pre_auth["required"] and pre_auth["status"] == "PASS" and pre_auth["authorized_paise"] is not None:
        status = "AUTHORIZED_BY_PRE_AUTH"
    elif pre_auth["required"] and pre_auth["status"] == "PASS":
        status = "NOT_EVALUATED"
        claim.reason("PRE_AUTH_AMOUNT_UNVERIFIED", f"The eligible amount ₹{_rupees(eligible)} exceeds the ₹{_rupees(ceiling)} per-claim limit and the approval record states no authorized amount. Verify the pre-authorized amount before payment.")
    elif pre_auth["required"]:
        status = "DEFERRED_TO_PRE_AUTH"
    else:
        status = "FAIL"
        claim.reason("PER_CLAIM_EXCEEDED", f"Claimed amount ₹{_rupees(claim.claimed)} (eligible ₹{_rupees(eligible)}) exceeds the per-claim limit of ₹{_rupees(ceiling)} for {claim.category.lower()} claims.")
    claim.ceiling_status = status
    claim.trace.append(_rule_trace("per_claim_limit", status, category_policy["per_claim_ceiling_ref"], evidence))
    if status == "AUTHORIZED_BY_PRE_AUTH" and eligible > pre_auth["authorized_paise"]:
        reduction = eligible - pre_auth["authorized_paise"]
        claim.eligible -= reduction
        claim.benefit_limit_applied = True
        claim.ledger.append({"kind": "adjustment", "description": "Pre-authorized amount", "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": "pre_authorization"})


def _stage_pricing(claim: _Claim) -> None:
    """Network discount first, then co-pay (and branded co-pay) on the discounted amount."""
    category_policy = claim.category_policy
    category_ref = category_policy["policy_ref"]
    eligible = claim.eligible
    scale = Decimal(0) if claim.eligible_before_limits <= 0 else Decimal(eligible) / Decimal(claim.eligible_before_limits)
    branded_eligible = int((Decimal(claim.branded_items_paise) * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    hospital = claim_provider(claim.payload)
    match = _network_match(hospital, claim.policy) if hospital else None
    claim.network_percent = category_policy["network_discount_percent"] if match else 0
    claim.trace.append(_rule_trace("network_hospital", "PASS" if match else "NOT_EVALUATED" if not hospital else "NOT_APPLICABLE", "network_hospitals", {"hospital_name": hospital or None, "match": match, "match_rule": claim.policy["network_match_rule"], "discount_percent": claim.network_percent}))
    discount = _percentage(eligible, claim.network_percent)
    after_discount = eligible - discount
    claim.ledger.append({"kind": "adjustment", "description": "Network discount", "amount_paise": -discount, "amount": _rupees(-discount), "policy_ref": f"{category_ref}.network_discount_percent", "basis_paise": eligible, "percent": claim.network_percent})
    if category_policy["brand_status_values"] and claim.pharmacy_brand_unknown:
        claim.reason("PHARMACY_BRAND_STATUS_UNKNOWN", "The bill does not establish whether each medicine is branded or generic. Verify the product classification before applying pharmacy co-pay.")
    branded_lines = [item["description"] for item in claim.items if item.get("brand_status") == "BRANDED"]
    generic_mandatory = category_policy["generic_mandatory"]
    claim.trace.append(_rule_trace("generic_medicine_requirement", "FLAG" if generic_mandatory and branded_lines else "PASS", f"{category_ref}.generic_mandatory", {"generic_mandatory": generic_mandatory, "branded_lines": branded_lines}))
    if generic_mandatory and branded_lines:
        claim.reason("GENERIC_SUBSTITUTION_REVIEW", "The policy requires generic medicines where available. A reviewer must verify the documented need for the branded medicine.")
    copay_percent = category_policy["copay_percent"]
    base_copay = _percentage(after_discount, copay_percent)
    brand_percent = category_policy["branded_drug_copay_percent"]
    branded_after_discount = _percentage(branded_eligible, 100 - claim.network_percent) if branded_eligible else 0
    branded_base_copay = _percentage(branded_after_discount, copay_percent) if branded_after_discount else 0
    branded_copay = _percentage(branded_after_discount, brand_percent) if branded_after_discount and brand_percent is not None else 0
    copay = base_copay - branded_base_copay + branded_copay
    claim.payable = after_discount - copay
    claim.ledger.append({"kind": "adjustment", "description": "Member co-pay", "amount_paise": -(base_copay - branded_base_copay), "amount": _rupees(-(base_copay - branded_base_copay)), "policy_ref": f"{category_ref}.copay_percent", "basis_paise": after_discount - branded_after_discount, "percent": copay_percent})
    if branded_copay:
        claim.ledger.append({"kind": "adjustment", "description": "Branded medicine co-pay", "amount_paise": -branded_copay, "amount": _rupees(-branded_copay), "policy_ref": f"{category_ref}.branded_drug_copay_percent", "basis_paise": branded_after_discount, "percent": brand_percent})
    claim.trace.append({"stage": "pricing", "rule_id": "payable_amount", "status": "CALCULATED", "evidence": {"eligible_paise": eligible, "network_hospital": bool(match), "network_discount_paise": discount, "copay_paise": copay, "branded_basis_paise": branded_after_discount, "branded_copay_paise": branded_copay, "payable_paise": claim.payable}, "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."})


def _limit(claim: _Claim, rule_id: str, description: str, code: str, policy_ref: str, remaining: int, label: str) -> None:
    reduction = claim.payable - remaining
    claim.payable = remaining
    claim.benefit_limit_applied = True
    claim.limit_reasons.append({"code": code, "message": f"The remaining {label} limits this claim by ₹{_rupees(reduction)}."})
    claim.ledger.append({"kind": "adjustment", "description": description, "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": policy_ref, "rule_id": rule_id})


def _category_service_net(claim: _Claim) -> int | None:
    """Net benefit (after discount and co-pay) on the category's own service lines; None if not establishable."""
    category_policy = claim.category_policy
    if category_policy["service_scope"] == "all_eligible_lines":
        return claim.payable
    lines = [line for line in claim.ledger if line["kind"] == "line_item" and line["status"] == "ELIGIBLE"]
    if any(not line["itemized"] for line in lines):
        return None
    # Fail safe: only lines recognised as tests or medicines fall outside the service.
    gross = sum(
        line["amount_paise"] for line in lines
        if _term_hits(line["description"], category_policy["service_terms"])
        or not _term_hits(line["description"], category_policy["non_service_terms"])
    )
    after_discount = gross - _percentage(gross, claim.network_percent)
    return after_discount - _percentage(after_discount, category_policy["copay_percent"])


def _category_sub_limit(claim: _Claim) -> None:
    """Annual per-member cap on the net benefit for the category's own service lines (CATEGORY_SUB_LIMIT_RULE).

    This claim's own service benefit is a certain lower bound on the year's
    usage, so the cap is always applied to it; prior usage, when supplied,
    reduces what remains. A governing pre-authorization supersedes the cap.
    """
    category_policy = claim.category_policy
    category_ref = category_policy["policy_ref"]
    sub_limit = category_policy["sub_limit_paise"]
    used, usage_key, usage_basis = None, None, None
    for key, basis in SUB_LIMIT_USAGE_KEYS:
        if claim.inputs[key] is not None:
            used, usage_key, usage_basis = claim.inputs[key], key, basis
            break
    remaining = sub_limit if used is None else max(0, sub_limit - used)
    # A matched pre-authorization rule governs the amount instead (as for the per-claim ceiling).
    authorized = bool(claim.pre_auth["required"] and claim.pre_auth["status"] != "NOT_REQUIRED")
    service_net = None if authorized else _category_service_net(claim)
    counted = None
    if authorized:
        status = "AUTHORIZED_BY_PRE_AUTH" if claim.pre_auth["status"] == "PASS" else "DEFERRED_TO_PRE_AUTH"
    elif service_net is None:
        status = "NOT_EVALUATED" if claim.payable > remaining else "PASS"
    else:
        status = "LIMITED" if service_net > remaining else "PASS"
        counted = min(service_net, remaining)
    if status == "LIMITED" and service_net is not None:
        reduction = service_net - remaining
        claim.payable -= reduction
        claim.benefit_limit_applied = True
        scope = "consultation fees" if category_policy["service_scope"] == "matching_lines" else claim.category.lower() + " benefit"
        history = f"; ₹{_rupees(used)} was already used this policy year" if used else ""
        claim.limit_reasons.append({"code": "CATEGORY_SUB_LIMIT_LIMITED", "message": f"The {claim.category.lower()} sub-limit of ₹{_rupees(sub_limit)} a year for {scope} limits this claim by ₹{_rupees(reduction)}{history}."})
        claim.ledger.append({"kind": "adjustment", "description": f"{claim.category.title().replace('_', ' ')} sub-limit", "amount_paise": -reduction, "amount": _rupees(-reduction), "policy_ref": f"{category_ref}.sub_limit", "rule_id": "category_sub_limit"})
    elif status == "NOT_EVALUATED" and all(item["code"] in REVIEW_CODES for item in claim.reasons):
        # Nothing is paid on a rejection, so the unverified share only matters when the claim could pay.
        claim.reason("CATEGORY_SUB_LIMIT_UNVERIFIED", f"The {claim.category.lower()} sub-limit (₹{_rupees(sub_limit)} a year, ₹{_rupees(remaining)} remaining) applies to the {claim.category.lower()} service itself, and the bill is not itemized enough to establish that share. An operator must verify it before payment.")
    if used is None and not authorized:
        claim.factor("category_usage_not_evaluated", 0.03, ("payable",))
        claim.advise("CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED", f"Earlier {claim.category.lower()} benefit this policy year was not supplied; this claim was checked against the full ₹{_rupees(sub_limit)} {claim.category.lower()} sub-limit on its own.")
    claim.trace.append(_rule_trace(
        "category_sub_limit", status, f"{category_ref}.sub_limit",
        {
            "sub_limit": _rupees(sub_limit), "period": "policy_year_per_member", "service_scope": category_policy["service_scope"],
            "usage_key": usage_key, "usage_basis": usage_basis, "used": None if used is None else _rupees(used),
            "remaining_before_claim": _rupees(remaining), "service_net_payable": None if service_net is None else _rupees(service_net),
            "counted_against_sub_limit_paise": counted, "net_payable_after": _rupees(claim.payable),
            "interpretation": "CATEGORY_SUB_LIMIT_RULE",
        },
        "The bill is not itemized, so the category's own service share cannot be established." if status == "NOT_EVALUATED"
        else "Prior category usage not supplied; this claim was checked against the full sub_limit on its own." if used is None and not authorized else None,
    ))


def _stage_benefit_limits(claim: _Claim) -> None:
    """Benefit caps on the net payable: category sub_limit, annual OPD, sum insured, family floater."""
    limits = claim.policy["limits"]
    _category_sub_limit(claim)
    annual = limits["annual_opd_limit_paise"]
    ytd = claim.inputs["ytd"]
    remaining = None if ytd is None else max(0, annual - ytd)
    claim.trace.append({"stage": "policy", "rule_id": "annual_opd_limit", "status": "NOT_EVALUATED" if remaining is None else "PASS" if remaining >= claim.payable else "LIMITED", "policy_ref": "coverage.annual_opd_limit", "evidence": {"annual_limit": _rupees(annual), "ytd_claims_amount": claim.payload.get("ytd_claims_amount"), "ytd_source": claim.payload.get("ytd_claims_source", "claim_payload" if ytd is not None else None), "remaining": None if remaining is None else _rupees(remaining), "net_payable_before_limit": _rupees(claim.payable)}, "details": "Applied to the net payable after discount and co-pay." if remaining is not None else "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."})
    if remaining is None:
        claim.factor("annual_opd_usage_not_evaluated", 0.04, ("payable",))
        claim.advise("ANNUAL_LIMIT_NOT_EVALUATED", f"Year-to-date OPD usage was not supplied, so the ₹{_rupees(annual)} annual OPD limit was not evaluated. Payment is subject to the member's remaining annual OPD balance.")
    elif claim.payable > remaining:
        _limit(claim, "annual_opd_limit", "Annual OPD remaining limit", "ANNUAL_LIMIT_LIMITED", "coverage.annual_opd_limit", remaining, "annual OPD benefit")

    sum_insured = limits["sum_insured_per_employee_paise"]
    used = claim.inputs["sum_insured_used"]
    remaining = None if used is None else max(0, sum_insured - used)
    claim.trace.append(_rule_trace("sum_insured", "NOT_EVALUATED" if remaining is None else "PASS" if remaining >= claim.payable else "LIMITED", "coverage.sum_insured_per_employee", {"sum_insured_paise": sum_insured, "used_paise": used, "remaining_paise": remaining, "net_payable_before_limit_paise": claim.payable}, "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."))
    if remaining is not None and claim.payable > remaining:
        _limit(claim, "sum_insured", "Remaining sum insured limit", "SUM_INSURED_LIMITED", "coverage.sum_insured_per_employee", remaining, "sum insured")

    floater = limits["family_floater"]
    used = claim.inputs["family_floater_used"]
    remaining = None if used is None else max(0, floater["combined_limit_paise"] - used)
    claim.trace.append(_rule_trace("family_floater_limit", "NOT_EVALUATED" if remaining is None else "PASS" if remaining >= claim.payable else "LIMITED", "coverage.family_floater.combined_limit", {"enabled": floater["enabled"], "combined_limit_paise": floater["combined_limit_paise"], "used_paise": used, "remaining_paise": remaining, "net_payable_before_limit_paise": claim.payable}, "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."))
    if floater["enabled"] and remaining is not None and claim.payable > remaining:
        _limit(claim, "family_floater_limit", "Family floater remaining limit", "FAMILY_FLOATER_LIMITED", "coverage.family_floater.combined_limit", remaining, "family-floater benefit")


# --------------------------------------------------------------------------
# Decision and confidence
# --------------------------------------------------------------------------


def _relabel_unpaid_lines(claim: _Claim, decision: str, primary: str | None) -> None:
    """Lines on a non-payable outcome must not read as covered and payable."""
    lines = [line for line in claim.ledger if line["kind"] == "line_item"]
    if claim.claim_exclusions:
        labels = ", ".join(hit["label"] for hit in claim.claim_exclusions)
        for line in lines:
            if line["status"] != "EXCLUDED":
                line.update(line_check=line["status"], status="EXCLUDED", reason_code="EXCLUDED_CONDITION", reason=f"Excluded with the whole claim under the claim-level exclusion: {labels}.", policy_ref=claim.claim_exclusions[0]["source_paths"][0])
        return
    if decision in PAYABLE:
        return
    outcome = f"the claim was rejected ({primary})" if decision == "REJECTED" else "the claim was routed to manual review"
    for line in lines:
        if line["status"] == "ELIGIBLE":
            line.update(line_check="ELIGIBLE", status="NOT_ADJUDICATED", reason=f"Passed the line-level checks, but {outcome}; no amount is payable for this line.")


def _stage_decision(claim: _Claim) -> dict[str, Any]:
    codes = [reason["code"] for reason in claim.reasons]
    primary = next((code for code in REJECT_PRIORITY if code in codes), None)
    review_codes = list(dict.fromkeys(code for code in codes if code in REVIEW_CODES))
    payable = claim.payable
    if primary:
        decision, approved = "REJECTED", 0
    elif review_codes:
        decision, approved = "MANUAL_REVIEW", 0
    elif payable <= 0:
        decision, approved, primary = "REJECTED", 0, "NO_PAYABLE_AMOUNT"
        claim.reason("NO_PAYABLE_AMOUNT", "No payable amount remains after policy adjustments.")
        claim.reasons.extend(claim.limit_reasons)
    elif payable < claim.claimed and (claim.benefit_limit_applied or any(line["status"] in {"EXCLUDED", "NOT_COVERED"} for line in claim.ledger if line["kind"] == "line_item")):
        decision, approved = "PARTIAL", payable
    else:
        decision, approved = "APPROVED", payable
    lead = primary or (review_codes[0] if review_codes else None)
    if lead:
        claim.reasons.sort(key=lambda reason: reason["code"] != lead)
    if decision in PAYABLE:
        # Benefit-limit reductions explain a payable amount; on a rejection or
        # review they stay in the ledger and trace but are not reasons.
        claim.reasons.extend(claim.limit_reasons)
        claim.reasons.extend(claim.advisories)
    if not claim.reasons:
        claim.reason("COVERED", "Claim passed the evaluated document and policy checks.")
    _relabel_unpaid_lines(claim, decision, primary)
    score = _stage_confidence(claim, decision, primary)
    claim.trace.append({"stage": "decision", "rule_id": "outcome", "status": decision, "evidence": {"primary_reason": lead, "review_reasons": sorted(review_codes), "approved_amount_paise": approved, "post_decision_review_recommended": claim.component_degraded}})
    return claim.result(state="DECIDED" if decision != "MANUAL_REVIEW" else "MANUAL_REVIEW", decision=decision, approved_amount=_rupees(approved), approved_amount_paise=approved, confidence_score=score)


def _stage_confidence(claim: _Claim, decision: str, primary: str | None) -> float:
    """Deduct each factor only when it is material to the outcome reached."""
    classes = {"payable"} if decision in PAYABLE else {"review"} if decision == "MANUAL_REVIEW" else {"rejection"}
    if decision == "REJECTED":
        if primary in IDENTITY_DEPENDENT_REJECTIONS:
            classes.add("identity_dependent_rejection")
        if primary in DATE_DEPENDENT_REJECTIONS:
            classes.add("date_dependent_rejection")
        if primary in AMOUNT_DEPENDENT_REJECTIONS:
            classes.add("amount_dependent_rejection")
    for item in claim.factors:
        item["applied"] = bool(classes & set(item["applies_to"]))
    score = round(max(0.0, CONFIDENCE_BASE - sum(item["points"] for item in claim.factors if item["applied"])), 2)
    claim.trace.append({"stage": "confidence", "rule_id": "confidence_rubric", "status": "DEGRADED" if score < CONFIDENCE_BASE else "PASS", "evidence": {"base": CONFIDENCE_BASE, "outcome_classes": sorted(classes), "factors": claim.factors, "score": score}, "details": CONFIDENCE_NOTE})
    return score


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

_GATE_STAGES = (_stage_intake, _stage_document_gate, _stage_eligibility, _stage_identity, _stage_bill_reconciliation)
_POLICY_STAGES = (
    _stage_dates, _stage_coverage, _stage_waiting_periods, _stage_claim_exclusions, _stage_pre_authorization,
    _stage_risk_thresholds, _stage_risk_enrichment, _stage_line_items, _stage_category_rules,
    _stage_per_claim_ceiling, _stage_pricing, _stage_benefit_limits,
)


def _evaluate_claim(payload: dict[str, Any], policy: dict[str, Any], enricher: RiskEnricher) -> dict[str, Any]:
    """Run the stages in order. Repairable document problems return decision=None."""
    claim = _Claim(payload=payload, policy=policy, enricher=enricher)
    claim.trace.append(_policy_source_step(policy))
    claim.category = str(payload.get("claim_category", "")).upper()
    claim.documents = payload.get("documents") or []
    for gate in _GATE_STAGES:
        early = gate(claim)
        if early is not None:
            return early
    for stage in _POLICY_STAGES:
        stage(claim)
    return _stage_decision(claim)


def _review_result(code: str, message: str, step: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": "MANUAL_REVIEW", "decision": "MANUAL_REVIEW",
        "approved_amount": 0, "approved_amount_paise": 0,
        "reasons": [{"code": code, "message": message}],
        "correction_requests": [], "confidence_score": 0.0, "trace": [step], "ledger": [],
    }


def evaluate_claim(
    payload: dict[str, Any],
    policy: dict[str, Any],
    *,
    optional_risk_enricher: RiskEnricher = risk_signal_enrichment,
) -> dict[str, Any]:
    """Evaluate one claim, routing malformed evidence or policy to review with a trace.

    ``optional_risk_enricher`` defaults to the production risk-signal component;
    tests and the evaluator replace it only to inject a failure.
    """
    try:
        canonical = ensure_canonical(policy)
    except PolicyConfigurationError as exc:
        return _review_result(
            PolicyConfigurationError.code,
            "The policy configuration failed validation; no claim can be adjudicated until it is corrected.",
            {"stage": "configuration", "rule_id": "policy_schema", "status": "FAIL", "error_type": type(exc).__name__, "details": str(exc)[:500]},
        )
    try:
        return _evaluate_claim(payload, canonical, optional_risk_enricher)
    except InvalidClaimInput as exc:
        return _review_result(
            "MALFORMED_EVIDENCE", "Claim evidence could not be validated; manual review is required.",
            {"stage": "validation", "rule_id": "input_schema", "status": "FAIL", "error_type": type(exc).__name__, "field": exc.field_name, "details": str(exc)[:200]},
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ArithmeticError) as exc:
        return _review_result(
            "MALFORMED_EVIDENCE", "Claim evidence could not be validated; manual review is required.",
            {"stage": "validation", "rule_id": "input_schema", "status": "FAIL", "error_type": type(exc).__name__, "details": "Malformed input prevented safe adjudication."},
        )
