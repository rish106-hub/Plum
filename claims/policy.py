"""Strict loading and normalization of the supplied policy terms.

``data/policy_terms.json`` is a read-only evaluator artifact. It is parsed by a
strict Pydantic schema (missing or mistyped limits fail fast), then converted by
``PolicyNormalizer`` into one canonical configuration that the claim engine
consumes. Everything the engine needs that the policy text does not state
literally -- synonyms, name variants, contradictory duplicates, dangling roster
references -- is resolved here, in labelled interpretation tables, and recorded
in an audit trail with the source JSON paths it touched. The raw policy file is
never edited to make the engine work.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CANONICAL_SCHEMA = "plum.canonical_policy.v1"
DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "policy_terms.json"


class PolicyConfigurationError(ValueError):
    """The supplied policy cannot be used safely for adjudication."""

    code = "POLICY_CONFIGURATION_INVALID"

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(f"{self.code}: {message}")
        self.errors = errors or []


# --------------------------------------------------------------------------
# Raw schema: mirrors the supplied JSON exactly. Unknown keys are forbidden so
# an edited policy (for example re-added alias tables) fails instead of being
# silently ignored or silently honoured.
# --------------------------------------------------------------------------

Money = Annotated[int, Field(strict=True, ge=0)]
Percent = Annotated[int, Field(strict=True, ge=0, le=100)]
Days = Annotated[int, Field(strict=True, ge=0)]
Count = Annotated[int, Field(strict=True, ge=1)]
Flag = Annotated[bool, Field(strict=True)]
Text = Annotated[str, Field(strict=True, min_length=1)]
DocumentType = Literal[
    "PRESCRIPTION", "HOSPITAL_BILL", "PHARMACY_BILL", "LAB_REPORT",
    "DIAGNOSTIC_REPORT", "DISCHARGE_SUMMARY", "DENTAL_REPORT",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RawPolicyHolder(_Strict):
    company_name: Text
    employee_count: Count
    policy_start_date: date
    policy_end_date: date
    renewal_status: Text


class RawFamilyFloater(_Strict):
    enabled: Flag
    combined_limit: Money
    covered_relationships: list[Text] = Field(min_length=1)


class RawCoverage(_Strict):
    sum_insured_per_employee: Money
    annual_opd_limit: Money
    per_claim_limit: Money
    family_floater: RawFamilyFloater


class RawOpdCategory(_Strict):
    sub_limit: Money
    copay_percent: Percent
    covered: Flag
    requires_prescription: Flag
    network_discount_percent: Percent | None = None
    requires_pre_auth: Flag | None = None
    pre_auth_threshold: Money | None = None
    high_value_tests_requiring_pre_auth: list[Text] | None = None
    branded_drug_copay_percent: Percent | None = None
    generic_mandatory: Flag | None = None
    requires_dental_report: Flag | None = None
    covered_procedures: list[Text] | None = None
    excluded_procedures: list[Text] | None = None
    covered_items: list[Text] | None = None
    excluded_items: list[Text] | None = None
    requires_registered_practitioner: Flag | None = None
    max_sessions_per_year: Count | None = None
    covered_systems: list[Text] | None = None


class RawWaitingPeriods(_Strict):
    initial_waiting_period_days: Days
    pre_existing_conditions_days: Days
    specific_conditions: dict[str, Days]


class RawExclusions(_Strict):
    conditions: list[Text]
    dental_exclusions: list[Text]
    vision_exclusions: list[Text]


class RawPreAuthorization(_Strict):
    required_for: list[Text]
    validity_days: Count


class RawSubmissionRules(_Strict):
    deadline_days_from_treatment: Count
    minimum_claim_amount: Money
    currency: Literal["INR"]


class RawDocumentRequirement(_Strict):
    required: list[DocumentType] = Field(min_length=1)
    optional: list[DocumentType]


class RawFraudThresholds(_Strict):
    same_day_claims_limit: Count
    monthly_claims_limit: Count
    high_value_claim_threshold: Money
    auto_manual_review_above: Money
    fraud_score_manual_review_threshold: Annotated[float, Field(ge=0, le=1)]

    @field_validator("fraud_score_manual_review_threshold", mode="before")
    @classmethod
    def _numeric(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number between 0 and 1")
        return value


class RawMember(_Strict):
    member_id: Text
    name: Text
    date_of_birth: date
    gender: Literal["M", "F", "O"]
    relationship: Text
    join_date: date | None = None
    dependents: list[Text] | None = None
    primary_member_id: Text | None = None

    @model_validator(mode="after")
    def _roster_shape(self) -> RawMember:
        if self.primary_member_id is None and self.join_date is None:
            raise ValueError(f"primary member {self.member_id} needs a join_date")
        if self.primary_member_id is not None and self.dependents:
            raise ValueError(f"dependent {self.member_id} cannot list its own dependents")
        return self


class RawPolicy(_Strict):
    policy_id: Text
    policy_name: Text
    insurer: Text
    policy_holder: RawPolicyHolder
    coverage: RawCoverage
    opd_categories: dict[str, RawOpdCategory] = Field(min_length=1)
    waiting_periods: RawWaitingPeriods
    exclusions: RawExclusions
    pre_authorization: RawPreAuthorization
    network_hospitals: list[Text]
    submission_rules: RawSubmissionRules
    document_requirements: dict[str, RawDocumentRequirement]
    fraud_thresholds: RawFraudThresholds
    members: list[RawMember] = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_references(self) -> RawPolicy:
        if self.policy_holder.policy_end_date <= self.policy_holder.policy_start_date:
            raise ValueError("policy_holder.policy_end_date must be after policy_start_date")
        categories = {key.upper() for key in self.opd_categories}
        documented = set(self.document_requirements)
        if categories != documented:
            raise ValueError(
                "opd_categories and document_requirements must name the same categories; "
                f"only in opd_categories: {sorted(categories - documented)}, "
                f"only in document_requirements: {sorted(documented - categories)}"
            )
        ids = [member.member_id for member in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("members contains duplicate member_id values")
        return self


# --------------------------------------------------------------------------
# Interpretation tables. These are engine judgments, not policy text. Every
# entry used is emitted into the canonical audit trail with provenance, and
# every canonical term carries provenance "policy_text" (its words appear in
# the supplied wording) or "interpretation" (it comes from one of these tables).
# --------------------------------------------------------------------------

INTERPRETATION_EXCLUSION_TERMS: dict[str, list[str]] = {
    "Self-inflicted injuries": ["self-harm", "deliberate self harm"],
    "War or nuclear hazard": ["war", "nuclear hazard", "war injury"],
    "Substance abuse treatment": ["substance abuse", "de-addiction", "drug rehabilitation", "alcohol dependence"],
    "Experimental treatments": ["experimental treatment", "experimental therapy", "investigational treatment"],
    "Infertility and assisted reproduction": ["infertility", "assisted reproduction", "IVF", "in vitro fertilisation", "in vitro fertilization"],
    "Obesity and weight loss programs": ["obesity", "weight loss program", "weight loss", "weight management", "bariatric"],
    "Bariatric surgery": ["gastric bypass", "sleeve gastrectomy"],
    "Cosmetic or aesthetic procedures": ["aesthetic procedure", "cosmetic procedure", "cosmetic surgery", "cosmetic treatment"],
    "Health supplements and tonics": ["health supplement", "supplement", "tonic", "multivitamin"],
    "Teeth whitening": ["tooth whitening"],
    "Orthodontic Treatment (Braces)": ["orthodontic treatment", "braces"],
    "Implants (Cosmetic)": ["cosmetic implant"],
    "Vaccination (non-medically necessary)": ["vaccination", "vaccine"],
}
"""Terms that identify each supplied exclusion. Terms not literally in the label are interpretation."""

INTERPRETATION_QUALIFIED_EXCLUSIONS: dict[str, str] = {
    "Vaccination (non-medically necessary)": "non-medically necessary",
}
"""Exclusions whose parenthetical is a clinical qualifier the documents cannot settle; a hit routes to review."""

INTERPRETATION_CONDITION_TERMS: dict[str, list[str]] = {
    "diabetes": ["diabetic", "diabetes mellitus", "T2DM", "T1DM"],
    "hypertension": ["HTN", "high blood pressure"],
    "thyroid_disorders": ["thyroid", "hypothyroidism", "hyperthyroidism", "thyroiditis"],
    "joint_replacement": ["knee replacement", "hip replacement", "arthroplasty"],
    "maternity": ["pregnancy", "antenatal", "prenatal", "obstetric"],
    "mental_health": ["depression", "anxiety disorder", "psychiatric"],
    "obesity_treatment": ["obesity", "bariatric", "weight loss"],
    "hernia": ["inguinal hernia", "umbilical hernia"],
    "cataract": ["cataract surgery"],
}
"""Clinical synonyms for waiting-period condition keys (keys come from the policy)."""

INTERPRETATION_PRE_AUTH_TERMS: dict[str, list[str]] = {
    "mri scan": ["MRI", "magnetic resonance imaging"],
    "ct scan": ["CT", "computed tomography"],
    "pet scan": ["PET", "PET-CT", "positron emission tomography"],
    "major surgical procedures": ["major surgery"],
    "planned hospitalization": ["planned hospitalisation", "planned admission"],
}
"""Short forms of the free-text pre-authorization items, keyed by normalized item label."""

INTERPRETATION_NETWORK_HOSPITAL_VARIANTS: dict[str, list[str]] = {
    "Apollo Hospitals": ["Apollo Hospital"],
    "Fortis Healthcare": ["Fortis Hospital", "Fortis Hospitals"],
    "Max Healthcare": ["Max Hospital", "Max Super Speciality Hospital"],
    "Manipal Hospitals": ["Manipal Hospital"],
    "Narayana Health": ["Narayana Hrudayalaya"],
    "Medanta": ["Medanta The Medicity"],
    "Kokilaben Dhirubhai Ambani Hospital": ["Kokilaben Hospital"],
    "Aster CMI Hospital": ["Aster CMI"],
    "Columbia Asia": ["Columbia Asia Hospital"],
    "Sakra World Hospital": ["Sakra Hospital"],
}
"""Common printed variants of network provider names."""

INTERPRETATION_NETWORK_MATCH_RULE = "exact_or_branch_suffix"
"""A provider matches when its name equals a network term, or equals it before a
comma / dash separated branch or city suffix (``Apollo Hospitals, Bengaluru``)."""

INTERPRETATION_COVERED_SYSTEM_TERMS: dict[str, list[str]] = {
    "Ayurveda": ["ayurvedic", "panchakarma", "vaidya"],
    "Homeopathy": ["homoeopathy", "homeopathic", "homoeopathic"],
    "Unani": ["hakim"],
    "Siddha": [],
    "Naturopathy": ["naturopathic", "nature cure"],
}
"""Therapies and practitioner titles that identify a covered AYUSH system."""

INTERPRETATION_RELATIONSHIPS: dict[str, str] = {
    "CHILD": "CHILDREN", "SON": "CHILDREN", "DAUGHTER": "CHILDREN",
    "PARENT": "PARENTS", "FATHER": "PARENTS", "MOTHER": "PARENTS",
    "WIFE": "SPOUSE", "HUSBAND": "SPOUSE",
}
"""Singular roster relationships mapped to the plural family-floater vocabulary."""

INTERPRETATION_BRAND_STATUS_VALUES = ["BRANDED", "GENERIC"]
"""Line-level classification needed to apply branded_drug_copay_percent."""


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------


def _words(value: Any) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE))


_KEEP_PLURAL = {"diabetes", "rabies", "scabies", "measles", "herpes", "series", "species", "braces"}


def _singular_word(word: str) -> str:
    if len(word) <= 3 or word in _KEEP_PLURAL or not word.endswith("s") or word.endswith(("ss", "is", "us")):
        return word
    return word[:-3] + "y" if word.endswith("ies") else word[:-1]


def _singular(phrase: str) -> str | None:
    words = phrase.split()
    if not words:
        return None
    last = _singular_word(words[-1])
    return None if last == words[-1] else " ".join([*words[:-1], last])


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def _provenance(term: str, grounding: list[str]) -> str:
    """``policy_text`` when the term's words appear verbatim (up to plurals) in a supplied string."""
    normalized = _words(term)
    singular = " ".join(_singular_word(word) for word in normalized.split())
    for source in grounding:
        source_words = _words(source)
        source_singular = " ".join(_singular_word(word) for word in source_words.split())
        if _contains(source_words, normalized) or _contains(source_singular, singular):
            return "policy_text"
    return "interpretation"


def _terms(texts: list[str], grounding: list[str]) -> list[dict[str, str]]:
    """Unique match terms with singular variants and provenance."""
    seen: dict[str, dict[str, str]] = {}
    for text in texts:
        for candidate in (text, _singular(_words(text))):
            if not candidate:
                continue
            key = _words(candidate)
            if key and key not in seen:
                seen[key] = {"text": key, "provenance": _provenance(candidate, grounding)}
    return list(seen.values())


def _strip_parenthetical(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", label).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PolicyNormalizer:
    """Convert a validated raw policy into the canonical engine configuration."""

    def __init__(self, raw: RawPolicy, *, source_sha256: str, source_path: str | None, source_kind: str) -> None:
        self.raw = raw
        self.source = {"sha256": source_sha256, "path": source_path, "kind": source_kind}
        self.audit: list[dict[str, Any]] = []

    def _record(self, entry_id: str, kind: str, paths: list[str], description: str, **extra: Any) -> None:
        self.audit.append({"id": entry_id, "kind": kind, "source_paths": paths, "description": description, **extra})

    def normalize(self) -> dict[str, Any]:
        raw = self.raw
        canonical: dict[str, Any] = {
            "schema_version": CANONICAL_SCHEMA,
            "policy_id": raw.policy_id,
            "policy_name": raw.policy_name,
            "insurer": raw.insurer,
            "source": self.source,
            "policy_holder": {
                "company_name": raw.policy_holder.company_name,
                "policy_start_date": raw.policy_holder.policy_start_date.isoformat(),
                "policy_end_date": raw.policy_holder.policy_end_date.isoformat(),
                "renewal_status": raw.policy_holder.renewal_status.strip().upper(),
            },
            "limits": self._limits(),
            "categories": self._categories(),
            "document_requirements": {
                category: {"required": list(item.required), "optional": list(item.optional)}
                for category, item in raw.document_requirements.items()
            },
            "waiting_periods": self._waiting_periods(),
            "exclusions": self._exclusions(),
            "pre_authorization": self._pre_authorization(),
            "network_hospitals": self._network_hospitals(),
            "network_match_rule": INTERPRETATION_NETWORK_MATCH_RULE,
            "submission_rules": {
                "deadline_days": raw.submission_rules.deadline_days_from_treatment,
                "minimum_claim_amount_paise": raw.submission_rules.minimum_claim_amount * 100,
                "currency": raw.submission_rules.currency,
            },
            "fraud_thresholds": {
                "same_day_claims_limit": raw.fraud_thresholds.same_day_claims_limit,
                "monthly_claims_limit": raw.fraud_thresholds.monthly_claims_limit,
                "high_value_claim_threshold_paise": raw.fraud_thresholds.high_value_claim_threshold * 100,
                "auto_manual_review_above_paise": raw.fraud_thresholds.auto_manual_review_above * 100,
                "fraud_score_manual_review_threshold": str(raw.fraud_thresholds.fraud_score_manual_review_threshold),
            },
            "members": self._members(),
        }
        self._record(
            "NETWORK_MATCH_RULE", "interpretation", ["network_hospitals"],
            "Provider names match a network hospital exactly, or before a comma/dash branch or city suffix.",
        )
        canonical["audit"] = self.audit
        canonical["canonical_sha256"] = sha256_bytes(
            json.dumps({k: v for k, v in canonical.items() if k != "audit"}, sort_keys=True).encode("utf-8")
        )
        return canonical

    def _limits(self) -> dict[str, Any]:
        coverage = self.raw.coverage
        self._record(
            "AGGREGATE_LIMITS_NEED_UTILISATION", "interpretation",
            ["coverage.annual_opd_limit", "coverage.sum_insured_per_employee", "coverage.family_floater.combined_limit"],
            "Annual OPD limit, sum insured and family floater are cross-claim aggregates. They are applied when a "
            "utilisation figure accompanies the claim; otherwise the rule is NOT_EVALUATED, disclosed as an advisory "
            "reason, and lowers confidence on payable outcomes. They never block an otherwise decidable claim.",
        )
        return {
            "per_claim_limit_paise": coverage.per_claim_limit * 100,
            "annual_opd_limit_paise": coverage.annual_opd_limit * 100,
            "sum_insured_per_employee_paise": coverage.sum_insured_per_employee * 100,
            "family_floater": {
                "enabled": coverage.family_floater.enabled,
                "combined_limit_paise": coverage.family_floater.combined_limit * 100,
                "covered_relationships": [value.strip().upper() for value in coverage.family_floater.covered_relationships],
            },
        }

    def _categories(self) -> dict[str, Any]:
        raw = self.raw
        global_limit = raw.coverage.per_claim_limit
        categories: dict[str, Any] = {}
        for key, item in raw.opd_categories.items():
            category = key.upper()
            ref = f"opd_categories.{key}"
            if item.sub_limit > global_limit:
                ceiling, ceiling_ref = item.sub_limit, f"{ref}.sub_limit"
                note = "category sub_limit exceeds the global per-claim limit and supersedes it for this category"
            else:
                ceiling, ceiling_ref = global_limit, "coverage.per_claim_limit"
                note = (
                    "global per-claim limit applies; the lower category sub_limit is not applied as a per-claim cap "
                    "(see PER_CLAIM_CEILING_RULE)" if item.sub_limit < global_limit else "sub_limit equals the global limit"
                )
            self._record(
                f"PER_CLAIM_CEILING.{category}", "derived", ["coverage.per_claim_limit", f"{ref}.sub_limit"],
                f"{category} per-claim ceiling is Rs {ceiling}: {note}.",
                ceiling=ceiling,
            )
            if item.network_discount_percent is None:
                self._record(
                    f"NO_NETWORK_DISCOUNT.{category}", "absent_optional_field", [f"{ref}.network_discount_percent"],
                    f"{category} declares no network discount; 0% is applied.",
                )
            if item.requires_pre_auth is None:
                self._record(
                    f"CATEGORY_PRE_AUTH_FLAG_ABSENT.{category}", "absent_optional_field", [f"{ref}.requires_pre_auth"],
                    f"{category} has no category-wide pre-authorization flag; item rules in pre_authorization.required_for still apply.",
                )
            advisory_documents: list[str] = []
            if item.requires_dental_report:
                optional = raw.document_requirements[category].optional
                advisory_documents.append("DENTAL_REPORT")
                self._record(
                    f"DENTAL_REPORT_CONFLICT.{category}", "conflict_resolution",
                    [f"{ref}.requires_dental_report", f"document_requirements.{category}.optional"],
                    "requires_dental_report=true conflicts with document_requirements listing DENTAL_REPORT as "
                    f"optional ({'listed' if 'DENTAL_REPORT' in optional else 'not listed'}). The document matrix governs "
                    "the correction gate; an absent report is traced as ADVISORY and lowers confidence on payable outcomes.",
                )
            covered_items = [*(item.covered_procedures or []), *(item.covered_items or [])]
            systems = []
            for name in item.covered_systems or []:
                extra = INTERPRETATION_COVERED_SYSTEM_TERMS.get(name, [])
                systems.append({"name": name, "terms": _terms([name, *extra], [name])})
                if extra:
                    self._record(
                        f"COVERED_SYSTEM_TERMS.{name}", "interpretation", [f"{ref}.covered_systems"],
                        f"{name} is also recognised from: {', '.join(extra)}.",
                    )
            brand = item.branded_drug_copay_percent is not None
            if brand:
                self._record(
                    f"BRAND_CLASSIFICATION.{category}", "interpretation",
                    [f"{ref}.branded_drug_copay_percent", f"{ref}.generic_mandatory"],
                    "Branded co-pay requires each medicine line to be classified "
                    f"{'/'.join(INTERPRETATION_BRAND_STATUS_VALUES)} with supporting text; unclassified lines route to review.",
                )
            categories[category] = {
                "key": key,
                "policy_ref": ref,
                "covered": item.covered,
                "sub_limit_paise": item.sub_limit * 100,
                "per_claim_ceiling_paise": ceiling * 100,
                "per_claim_ceiling_ref": ceiling_ref,
                "copay_percent": item.copay_percent,
                "network_discount_percent": item.network_discount_percent or 0,
                "requires_prescription": item.requires_prescription,
                "requires_pre_auth": bool(item.requires_pre_auth),
                "branded_drug_copay_percent": item.branded_drug_copay_percent,
                "generic_mandatory": bool(item.generic_mandatory),
                "brand_status_values": list(INTERPRETATION_BRAND_STATUS_VALUES) if brand else [],
                "covered_items": [{"label": label, "terms": _terms([label], [label])} for label in covered_items],
                "covered_items_ref": f"{ref}.covered_procedures" if item.covered_procedures else f"{ref}.covered_items",
                "requires_registered_practitioner": bool(item.requires_registered_practitioner),
                "max_sessions_per_year": item.max_sessions_per_year,
                "covered_systems": systems,
                "advisory_documents": advisory_documents,
            }
        self._record(
            "PER_CLAIM_CEILING_RULE", "conflict_resolution",
            ["coverage.per_claim_limit", "opd_categories.*.sub_limit"],
            "Each category's per-claim ceiling is max(coverage.per_claim_limit, category sub_limit), tested against the "
            "eligible amount after excluded/non-covered lines are removed; exceeding it rejects the claim "
            "(PER_CLAIM_EXCEEDED). Where a matched pre-authorization rule governs the treatment, the pre-authorization "
            "decides instead. Consultation's sub_limit (Rs 2,000) is below the global limit and is therefore not a "
            "per-claim cap; a literal cap would contradict the supplied network consultation outcome.",
        )
        return categories

    def _waiting_periods(self) -> dict[str, Any]:
        waiting = self.raw.waiting_periods
        conditions = []
        for key, days in waiting.specific_conditions.items():
            literal = key.replace("_", " ")
            extra = INTERPRETATION_CONDITION_TERMS.get(key, [])
            conditions.append({
                "condition": key, "days": days, "policy_ref": f"waiting_periods.specific_conditions.{key}",
                "terms": _terms([literal, *extra], [literal]),
            })
            if extra:
                self._record(
                    f"CONDITION_TERMS.{key}", "interpretation", [f"waiting_periods.specific_conditions.{key}"],
                    f"{literal} waiting period is also triggered by: {', '.join(extra)}.",
                )
        return {
            "initial_days": waiting.initial_waiting_period_days,
            "pre_existing_days": waiting.pre_existing_conditions_days,
            "specific_conditions": conditions,
        }

    def _exclusions(self) -> list[dict[str, Any]]:
        """Merge the three exclusion representations into one list.

        ``exclusions.conditions`` apply to the whole claim and to every line.
        ``exclusions.dental_exclusions`` / ``vision_exclusions`` and the
        category ``excluded_procedures`` / ``excluded_items`` describe the same
        category-scoped line exclusions and are merged when one label contains
        the other (``LASIK`` / ``LASIK Surgery``).
        """
        raw = self.raw
        sources: list[tuple[str, str, str]] = []
        for index, label in enumerate(raw.exclusions.conditions):
            sources.append(("ALL", label, f"exclusions.conditions[{index}]"))
        scoped = {"DENTAL": raw.exclusions.dental_exclusions, "VISION": raw.exclusions.vision_exclusions}
        for scope, labels in scoped.items():
            for index, label in enumerate(labels):
                sources.append((scope, label, f"exclusions.{scope.lower()}_exclusions[{index}]"))
        for key, item in raw.opd_categories.items():
            for field in ("excluded_procedures", "excluded_items"):
                for index, label in enumerate(getattr(item, field) or []):
                    sources.append((key.upper(), label, f"opd_categories.{key}.{field}[{index}]"))

        merged: list[dict[str, Any]] = []
        for scope, label, path in sources:
            core = _words(_strip_parenthetical(label))
            target = next(
                (
                    entry for entry in merged
                    if entry["scope"] == scope and (_contains(entry["_core"], core) or _contains(core, entry["_core"]))
                ),
                None,
            )
            if target is None:
                target = {"_core": core, "scope": scope, "labels": [], "source_paths": []}
                merged.append(target)
            else:
                self._record(
                    f"EXCLUSION_MERGED.{scope}.{core.replace(' ', '_')}", "conflict_resolution",
                    [*target["source_paths"], path],
                    f"'{label}' duplicates '{target['labels'][0]}' for {scope}; merged into one canonical exclusion.",
                )
            target["labels"].append(label)
            target["source_paths"].append(path)

        canonical = []
        for entry in merged:
            labels = entry["labels"]
            # A parenthetical changes meaning ("Implants (Cosmetic)" is not every
            # implant), so a bracketed label contributes only its interpreted terms.
            texts = [label for label in labels if "(" not in label]
            qualifier = None
            for label in labels:
                texts.extend(INTERPRETATION_EXCLUSION_TERMS.get(label, []))
                if "(" in label and label not in INTERPRETATION_EXCLUSION_TERMS:
                    texts.append(_strip_parenthetical(label))
                if label in INTERPRETATION_QUALIFIED_EXCLUSIONS:
                    qualifier = INTERPRETATION_QUALIFIED_EXCLUSIONS[label]
                elif "(" in label and label not in INTERPRETATION_EXCLUSION_TERMS:
                    qualifier = re.search(r"\(([^)]*)\)", label).group(1)  # type: ignore[union-attr]
                    self._record(
                        f"EXCLUSION_UNKNOWN_QUALIFIER.{label}", "interpretation", entry["source_paths"],
                        f"'{label}' carries a qualifier with no interpretation entry; matches route to review.",
                    )
                if label in INTERPRETATION_EXCLUSION_TERMS:
                    self._record(
                        f"EXCLUSION_TERMS.{label}", "interpretation", entry["source_paths"],
                        f"'{label}' is matched through: {', '.join(INTERPRETATION_EXCLUSION_TERMS[label])}.",
                    )
            exclusion_id = f"{entry['scope']}:{entry['_core'].replace(' ', '_')}"
            canonical.append({
                "id": exclusion_id,
                "label": labels[0],
                "scope": entry["scope"],
                "applies_to": ["claim", "line"] if entry["scope"] == "ALL" else ["line"],
                "terms": _terms(texts, labels),
                "qualifier": qualifier,
                "source_paths": entry["source_paths"],
            })
        return canonical

    def _pre_authorization(self) -> dict[str, Any]:
        raw = self.raw
        rules: list[dict[str, Any]] = []
        pattern = re.compile(r"^(?P<label>[^(]+?)\s*(?:\((?P<qualifier>[^)]*)\))?\s*$")
        amount_pattern = re.compile(r"^amount\s*>\s*(?:₹|rs\.?|inr)?\s*(?P<amount>[\d,]+)$", re.IGNORECASE)
        for index, text in enumerate(raw.pre_authorization.required_for):
            path = f"pre_authorization.required_for[{index}]"
            match = pattern.match(text)
            if match is None:
                raise PolicyConfigurationError(f"{path} cannot be parsed: {text!r}")
            label = match.group("label").strip()
            threshold = None
            if match.group("qualifier"):
                amount = amount_pattern.match(match.group("qualifier").strip())
                if amount is None:
                    raise PolicyConfigurationError(f"{path} has an unsupported qualifier: {text!r}")
                threshold = int(amount.group("amount").replace(",", ""))
            extra = INTERPRETATION_PRE_AUTH_TERMS.get(_words(label), [])
            rules.append({
                "id": _words(label).replace(" ", "_"), "label": label, "grounding": [label],
                "texts": [label, *extra], "amount_greater_than": threshold,
                "threshold_sources": [path] if threshold is not None else [],
                "source_paths": [path],
            })
            self._record(
                f"PRE_AUTH_PARSED.{rules[-1]['id']}", "derived", [path],
                f"'{text}' parsed as item '{label}'"
                + (f" requiring pre-authorization above Rs {threshold}." if threshold is not None else " always requiring pre-authorization.")
                + (f" Short forms: {', '.join(extra)}." if extra else ""),
            )
        for key, item in raw.opd_categories.items():
            tests = item.high_value_tests_requiring_pre_auth or []
            if item.pre_auth_threshold is not None and not tests:
                raise PolicyConfigurationError(f"opd_categories.{key}.pre_auth_threshold has no high_value_tests_requiring_pre_auth")
            for index, test in enumerate(tests):
                path = f"opd_categories.{key}.high_value_tests_requiring_pre_auth[{index}]"
                test_words = _words(test)
                rule = next(
                    (
                        candidate for candidate in rules
                        if any(_words(text) == test_words for text in candidate["texts"])
                    ),
                    None,
                )
                category_threshold = item.pre_auth_threshold
                if rule is None:
                    rules.append({
                        "id": test_words.replace(" ", "_"), "label": test, "grounding": [test], "texts": [test],
                        "amount_greater_than": category_threshold,
                        "threshold_sources": [f"opd_categories.{key}.pre_auth_threshold"], "source_paths": [path],
                    })
                    continue
                rule["grounding"].append(test)
                rule["source_paths"].append(path)
                if rule["amount_greater_than"] == category_threshold:
                    rule["threshold_sources"].append(f"opd_categories.{key}.pre_auth_threshold")
                    continue
                # "Always" (no threshold) is stricter than any threshold; otherwise the lower threshold is.
                if rule["amount_greater_than"] is None or category_threshold is None:
                    stricter = None
                else:
                    stricter = min(rule["amount_greater_than"], category_threshold)
                self._record(
                    f"PRE_AUTH_THRESHOLD_CONFLICT.{rule['id']}", "conflict_resolution",
                    [*rule["source_paths"], f"opd_categories.{key}.pre_auth_threshold"],
                    f"'{rule['label']}' threshold differs between pre_authorization.required_for "
                    f"({'always' if rule['amount_greater_than'] is None else 'above Rs ' + str(rule['amount_greater_than'])}) and "
                    f"opd_categories.{key}.pre_auth_threshold (above Rs {category_threshold}); the stricter reading "
                    f"({'always' if stricter is None else 'above Rs ' + str(stricter)}) is used.",
                )
                rule["amount_greater_than"] = stricter
        canonical_rules = []
        for rule in rules:
            canonical_rules.append({
                "id": rule["id"],
                "label": rule["label"],
                "terms": _terms(rule["texts"], rule["grounding"]),
                "amount_greater_than_paise": None if rule["amount_greater_than"] is None else rule["amount_greater_than"] * 100,
                "source_paths": list(dict.fromkeys(rule["source_paths"] + rule["threshold_sources"])),
            })
        return {"validity_days": raw.pre_authorization.validity_days, "rules": canonical_rules}

    def _network_hospitals(self) -> list[dict[str, Any]]:
        hospitals = []
        for index, name in enumerate(self.raw.network_hospitals):
            variants = INTERPRETATION_NETWORK_HOSPITAL_VARIANTS.get(name, [])
            hospitals.append({"name": name, "terms": _terms([name, *variants], [name]), "source_path": f"network_hospitals[{index}]"})
            if variants:
                self._record(
                    f"NETWORK_NAME_VARIANTS.{name}", "interpretation", [f"network_hospitals[{index}]"],
                    f"{name} is also recognised as: {', '.join(variants)}.",
                )
        return hospitals

    def _members(self) -> list[dict[str, Any]]:
        raw = self.raw
        by_id = {member.member_id: member for member in raw.members}
        covered_relationships = {value.strip().upper() for value in raw.coverage.family_floater.covered_relationships}
        members = []
        for index, member in enumerate(raw.members):
            path = f"members[{index}]"
            relationship = member.relationship.strip().upper()
            covered_relationship = relationship
            if relationship not in covered_relationships and relationship in INTERPRETATION_RELATIONSHIPS:
                covered_relationship = INTERPRETATION_RELATIONSHIPS[relationship]
                self._record(
                    f"RELATIONSHIP_VOCABULARY.{member.member_id}", "interpretation",
                    [f"{path}.relationship", "coverage.family_floater.covered_relationships"],
                    f"Roster relationship {relationship} is read as family-floater relationship {covered_relationship}.",
                )
            resolved, unresolved = [], []
            for dependent_index, dependent_id in enumerate(member.dependents or []):
                dependent = by_id.get(dependent_id)
                if dependent is not None and dependent.primary_member_id == member.member_id:
                    resolved.append(dependent_id)
                else:
                    unresolved.append(dependent_id)
                    self._record(
                        f"DANGLING_DEPENDENT.{member.member_id}.{dependent_id}", "reference_repair",
                        [f"{path}.dependents[{dependent_index}]"],
                        f"{member.member_id} lists dependent {dependent_id}, which is not in the roster "
                        "(or does not point back). It is excluded from covered-patient matching; a claim for it "
                        "cannot be verified and routes to review as an unknown member.",
                    )
            join_date = member.join_date
            join_source = f"{path}.join_date"
            primary = by_id.get(member.primary_member_id) if member.primary_member_id else None
            if member.primary_member_id is not None:
                if primary is None or member.member_id not in (primary.dependents or []):
                    self._record(
                        f"DANGLING_PRIMARY.{member.member_id}", "reference_repair", [f"{path}.primary_member_id"],
                        f"{member.member_id} points to primary {member.primary_member_id}, which does not list it; "
                        "the dependent is kept but has no inherited enrolment date unless it has its own.",
                    )
                if join_date is None and primary is not None and primary.join_date is not None:
                    join_date = primary.join_date
                    join_source = f"members[{raw.members.index(primary)}].join_date"
                    self._record(
                        f"DEPENDENT_JOIN_DATE.{member.member_id}", "derived", [path, join_source],
                        f"{member.member_id} has no join_date and inherits {primary.member_id}'s ({join_date.isoformat()}).",
                    )
            members.append({
                "member_id": member.member_id,
                "name": member.name,
                "relationship": relationship,
                "covered_relationship": covered_relationship,
                "primary_member_id": member.primary_member_id,
                "dependents": resolved,
                "unresolved_dependents": unresolved,
                "join_date": join_date.isoformat() if join_date else None,
                "join_date_source": join_source if join_date else None,
                "policy_ref": path,
            })
        return members


def _validation_error(exc: ValidationError) -> PolicyConfigurationError:
    errors = [
        {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    summary = "; ".join(f"{item['path'] or '<root>'}: {item['message']}" for item in errors[:8])
    return PolicyConfigurationError(summary, errors)


def normalize_policy(
    raw_policy: dict[str, Any], *, source_sha256: str | None = None, source_path: str | None = None
) -> dict[str, Any]:
    """Validate a raw policy mapping and return the canonical configuration."""
    if not isinstance(raw_policy, dict):
        raise PolicyConfigurationError("policy must be a JSON object")
    try:
        raw = RawPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    kind = "file" if source_sha256 else "in_memory"
    digest = source_sha256 or sha256_bytes(json.dumps(raw_policy, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return PolicyNormalizer(raw, source_sha256=digest, source_path=source_path, source_kind=kind).normalize()


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    """Read, validate and normalize a policy file; fail fast on any defect."""
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
        raw_policy = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyConfigurationError(f"cannot read {file_path.name}: {exc}") from None
    return normalize_policy(raw_policy, source_sha256=sha256_bytes(data), source_path=file_path.name)


def ensure_canonical(policy: dict[str, Any]) -> dict[str, Any]:
    """Return canonical config, normalizing a raw policy mapping if needed."""
    if isinstance(policy, dict) and policy.get("schema_version") == CANONICAL_SCHEMA:
        return policy
    return normalize_policy(policy)
