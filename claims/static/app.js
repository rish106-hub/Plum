(function () {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const page = document.body.dataset.page;

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatMoney(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "Not available";
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(amount);
  }

  function shortId(id) { return String(id || "").slice(0, 8).toUpperCase(); }

  function titleCase(value) {
    return String(value || "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  const FRIENDLY_LABELS = {
    annual_opd_limit: "Annual outpatient benefit limit", bill_arithmetic_conflict: "Bill total does not match its line items",
    candidate_evidence: "Model-supported evidence", claimed_amount: "Claimed amount", co_pay: "Member co-pay",
    consultation: "Consultation coverage", date: "Treatment date", decision_validation: "Decision consistency check",
    diagnosis: "Diagnosis", document_evidence: "Document evidence", document_extraction: "Document extraction",
    document_gate: "Required document check", document_type: "Document type", duplicate_check: "Duplicate bill check",
    evidence: "Supporting facts", extracted_facts: "Facts extracted from documents", extraction_source: "Extraction method",
    file_id: "Document reference", file_name: "Document name", fields: "Extracted facts", gemini_evidence: "Model evidence review",
    hospital_name: "Hospital or clinic", line_items: "Bill line items", matching_claim_count: "Similar claims found",
    matching_claim_ids: "Related claim references", member_id: "Member identifier", missing_document: "Required document is missing",
    patient_name: "Patient name", policy_ref: "Policy clause", previous_bill_hash: "Previously submitted bill match",
    quality: "Document quality", required_type: "Required document", root_cause: "Primary review reason",
    source: "Evidence source", sources: "Evidence sources", test_name: "Test or procedure", total: "Document total",
    total_paise: "Document total", waiting_period: "Waiting period check", ytd_claims_amount: "Approved this policy year",
  };

  const FRIENDLY_STATUSES = {
    ABSTAINED: "Model abstained", APPROVED: "Approved", BLOCKED: "Stopped",
    CANDIDATES_APPLIED: "Evidence verified", CANDIDATES_VALIDATED: "Evidence verified",
    DEGRADED: "Review needed", DISABLED: "Off", DOCUMENT_CORRECTION_REQUIRED: "Document correction",
    FAIL: "Failed", FAILED: "Failed", FLAG: "Review needed", MANUAL_REVIEW: "Manual review",
    NEEDS_CORRECTION: "Document correction", NOT_EVALUATED: "Not evaluated", NOT_NEEDED: "No model call needed",
    OK: "Passed", PARTIAL: "Partially approved", PASS: "Passed", PASSED: "Passed",
    PROCESSING: "Processing", PROCESSING_FAILED: "Processing failed", QUEUED: "Queued", RECORDED: "Recorded", REJECTED: "Rejected",
  };

  function friendlyLabel(value) {
    const key = String(value || "").toLowerCase();
    return FRIENDLY_LABELS[key] || titleCase(key).replace(" Id", " identifier").replace(" Paise", "");
  }

  function friendlyStatus(value) {
    const key = String(value || "").toUpperCase();
    return FRIENDLY_STATUSES[key] || titleCase(key);
  }

  function humanValue(value, keyName) {
    if (value === null || value === undefined || value === "") return "Not recorded";
    if (keyName === "policy_ref") return readablePolicyReference(value);
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "string" && /^UPLOAD-\d+$/i.test(value)) return `Submitted document ${value.split("-")[1]}`;
    if (typeof value === "string" && /^[A-Z][A-Z0-9_]+$/.test(value)) return friendlyLabel(value);
    if (typeof value !== "object") return String(value);
    if (Array.isArray(value)) return value.length ? value.map((item) => humanValue(item, keyName)).join("; ") : "None recorded";
    return Object.entries(value).map(([key, item]) => `${friendlyLabel(key)}: ${humanValue(item, key)}`).join(". ");
  }

  function readablePolicyReference(value) {
    const segments = String(value || "").split(/[.>\[\]\/]+/).filter(Boolean);
    return segments.length ? friendlyLabel(segments[segments.length - 1]) : "Policy terms";
  }

  function appendReadableFacts(parent, data) {
    const facts = make("dl", "readable-facts");
    const source = data && typeof data === "object" && !Array.isArray(data) ? data : { fact: data };
    Object.entries(source).forEach(([key, value]) => {
      if (["schema_version", "candidate_evidence"].includes(key)) return;
      const row = make("div");
      const valueNode = make("dd");
      appendFactValue(valueNode, value, key);
      row.append(make("dt", "", friendlyLabel(key)), valueNode);
      facts.append(row);
    });
    if (facts.children.length) parent.append(facts);
  }

  function appendFactValue(parent, value, keyName) {
    if (Array.isArray(value)) {
      if (!value.length) {
        parent.textContent = "None recorded";
        return;
      }
      const list = make("ul", "readable-facts__list");
      value.forEach((item) => {
        const listItem = make("li");
        appendFactValue(listItem, item, keyName);
        list.append(listItem);
      });
      parent.append(list);
      return;
    }
    if (value && typeof value === "object") {
      const nested = make("dl", "readable-facts readable-facts--nested");
      Object.entries(value).forEach(([key, item]) => {
        const row = make("div");
        const valueNode = make("dd");
        appendFactValue(valueNode, item, key);
        row.append(make("dt", "", friendlyLabel(key)), valueNode);
        nested.append(row);
      });
      parent.append(nested);
      return;
    }
    parent.textContent = humanValue(value, keyName);
  }

  function messageFromError(response) {
    return response.json().then((body) => {
      const detail = body && body.detail;
      if (typeof detail === "string") return detail;
      return "The request could not be completed. Check the details and try again.";
    }).catch(() => "The request could not be completed. Try again.");
  }

  async function loadRecent() {
    const list = byId("recent-claims");
    if (!list) return;
    try {
      const response = await fetch("/api/claims?limit=8", { cache: "no-store" });
      if (!response.ok) throw new Error("Recent claims unavailable");
      const claims = (await response.json()).claims || [];
      list.replaceChildren();
      if (!claims.length) {
        list.append(make("p", "muted", "No claims yet. The first submission will appear here."));
        return;
      }
      claims.forEach((claim) => {
        const row = make("a", "recent-row");
        row.href = `/claims/${encodeURIComponent(claim.id)}`;
        row.append(make("span", "recent-row__id", shortId(claim.id)));
        row.append(make("span", "recent-row__category", titleCase(claim.request.claim_category)));
        row.append(make("span", "recent-row__muted", claim.request.member_id));
        row.append(make("span", "", friendlyStatus(claim.decision || claim.state)));
        list.append(row);
      });
    } catch (_error) {
      list.replaceChildren(make("p", "muted", "Recent claims could not be loaded. Refresh to try again."));
    }
  }

  function setupIntake() {
    const form = byId("claim-form");
    const fileInput = byId("claim-files");
    const selected = byId("selected-files");
    const error = byId("form-error");
    const button = byId("submit-button");
    loadRecent();

    fileInput.addEventListener("change", () => {
      selected.replaceChildren();
      Array.from(fileInput.files).forEach((file) => {
        const row = make("li");
        row.append(make("span", "", file.name));
        row.append(make("span", "", `${(file.size / 1024 / 1024).toFixed(1)} MB`));
        selected.append(row);
      });
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.hidden = true;
      if (!form.reportValidity()) return;
      if (!fileInput.files.length) {
        error.textContent = "Choose at least one medical document.";
        error.hidden = false;
        return;
      }
      button.disabled = true;
      button.textContent = "Submitting claim…";
      try {
        const response = await fetch("/api/claims", { method: "POST", body: new FormData(form) });
        if (!response.ok) throw new Error(await messageFromError(response));
        const claim = await response.json();
        window.location.assign(claim.url);
      } catch (cause) {
        error.textContent = cause.message || "Submission failed. Try again.";
        error.hidden = false;
        button.disabled = false;
        button.textContent = "Check claim →";
      }
    });
  }

  function statePresentation(claim) {
    const decision = claim.result && claim.result.decision;
    if (["DOCUMENT_CORRECTION_REQUIRED", "NEEDS_CORRECTION"].includes(claim.state)) return ["Document correction", "correction"];
    if (claim.state === "PROCESSING_FAILED") return ["Processing failed", "failed"];
    if (!decision) return ["Processing", "processing"];
    const kind = { APPROVED: "approved", PARTIAL: "partial", REJECTED: "rejected", MANUAL_REVIEW: "review" }[decision] || "processing";
    return [titleCase(decision), kind];
  }

  function appendFact(parent, label, value) {
    const row = make("div");
    row.append(make("dt", "", label));
    row.append(make("dd", "", value));
    parent.append(row);
  }

  function traceKind(status) {
    const value = String(status || "").toUpperCase();
    if (["PASS", "PASSED", "OK", "APPROVED"].includes(value)) return "pass";
    if (["FAIL", "FAILED", "BLOCKED", "REJECTED"].includes(value)) return "fail";
    if (["WARNING", "NOT_EVALUATED", "DEGRADED", "ASSUMPTION"].includes(value) || value.startsWith("SKIPPED")) return "warning";
    return "neutral";
  }

  function renderTrace(trace) {
    const list = byId("trace-list");
    list.replaceChildren();
    if (!Array.isArray(trace) || !trace.length) {
      list.append(make("p", "empty-detail", "No trace was produced for this claim."));
      return;
    }
    trace.forEach((entry, index) => {
      const item = make("div", "trace-entry");
      const status = entry.status || entry.outcome || "Recorded";
      item.dataset.kind = traceKind(status);
      item.append(make("span", "trace-entry__mark"));
      const body = make("div");
      body.append(make("p", "trace-entry__title", friendlyLabel(entry.rule_id || entry.stage || `Check ${index + 1}`)));
      const reason = entry.reason || entry.message || entry.explanation || entry.details || entry.output;
      if (reason && (!Array.isArray(reason) || reason.length)) body.append(make("p", "trace-entry__reason", humanValue(reason)));
      const extra = Object.fromEntries(Object.entries(entry).filter(([key]) => !["rule_id", "stage", "status", "outcome", "reason", "message", "explanation", "details", "output"].includes(key)));
      if (Object.keys(extra).length) {
        const detail = make("details");
        detail.append(make("summary", "", "View supporting facts"));
        appendReadableFacts(detail, extra);
        body.append(detail);
      }
      item.append(body);
      item.append(make("span", "trace-entry__status", friendlyStatus(status)));
      list.append(item);
    });
  }

  function renderLedger(ledger) {
    const list = byId("ledger-list");
    list.replaceChildren();
    if (!Array.isArray(ledger) || !ledger.length) {
      list.append(make("p", "empty-detail", "No payable amount calculation was made."));
      return;
    }
    ledger.forEach((entry) => {
      const row = make("div", "ledger-row");
      const label = entry.description || entry.label || friendlyLabel(entry.rule_id || entry.stage || "Adjustment");
      const detail = make("span", "ledger-row__label", label);
      const context = [entry.status && friendlyStatus(entry.status), entry.reason_code && friendlyLabel(entry.reason_code), entry.source_document && `Source: ${entry.source_document}`, entry.policy_ref && `Policy: ${readablePolicyReference(entry.policy_ref)}`].filter(Boolean).join(" / ");
      if (context) detail.append(make("small", "ledger-row__context", context));
      row.append(detail);
      const paise = entry.amount_paise ?? entry.value_paise ?? entry.approved_amount_paise;
      const rupees = paise !== undefined && paise !== null ? Number(paise) / 100 : (entry.amount ?? entry.value);
      row.append(make("strong", "", formatMoney(rupees)));
      list.append(row);
    });
  }

  function renderEvidence(result, documents) {
    const list = byId("evidence-list");
    list.replaceChildren();
    const trace = Array.isArray(result.trace) ? result.trace : [];
    const candidates = trace.flatMap((entry) => Array.isArray(entry.candidate_evidence) ? entry.candidate_evidence : []);
    const namesByUpload = new Map((documents || []).map((document, index) => [`UPLOAD-${index + 1}`, document.original_name]));
    const evidence = candidates.flatMap((candidate) => (candidate.sources || []).map((source) => ({
      file: namesByUpload.get(candidate.file_id) || humanValue(candidate.file_id, "file_id") || "Submitted document",
      field: source.field,
      page: source.page,
      quote: source.quote,
    })).filter((source) => source.quote));
    if (!evidence.length) {
      list.append(make("p", "empty-detail", "No AI-sourced correction was applied. See the decision trace for rule evidence and document checks."));
      return;
    }
    evidence.forEach((source) => {
      const item = make("article", "evidence-entry");
      const meta = [friendlyLabel(source.field), source.file, source.page ? `Page ${source.page}` : "Page not recorded"].filter(Boolean).join(" / ");
      item.append(make("p", "evidence-entry__meta", meta));
      item.append(make("blockquote", "", `“${source.quote}”`));
      list.append(item);
    });
  }

  function renderEscalation(result) {
    const panel = byId("escalation-panel");
    const reasons = Array.isArray(result.reasons) ? result.reasons : [];
    const trace = Array.isArray(result.trace) ? result.trace : [];
    const reviewSignals = trace.filter((entry) => ["FLAG", "DEGRADED", "NOT_EVALUATED", "ASSUMPTION"].includes(String(entry.status || "").toUpperCase()));
    const isReview = result.decision === "MANUAL_REVIEW";
    panel.hidden = !isReview;
    if (!isReview) return;
    const primary = reasons[0];
    byId("escalation-reason").textContent = typeof primary === "string" ? primary : primary?.message || "A material fact could not be safely resolved automatically.";
    const signals = byId("escalation-signals");
    signals.replaceChildren();
    const readable = reviewSignals.slice(0, 4).map((entry) => `${friendlyLabel(entry.rule_id || entry.stage)}: ${humanValue(entry.reason || entry.details || friendlyStatus(entry.status))}`);
    (readable.length ? readable : ["A policy or evidence gate requires human verification."]).forEach((signal) => signals.append(make("li", "", signal)));
  }

  function renderAiMetrics(result) {
    const facts = byId("ai-facts");
    facts.replaceChildren();
    const documentMetrics = result.document_metrics || result.metrics || {};
    const metrics = documentMetrics.gemini || {};
    const geminiTrace = (result.trace || []).find((entry) => entry.stage === "gemini_evidence") || {};
    const usage = metrics.total_tokens || ((metrics.input_tokens || 0) + (metrics.output_tokens || 0));
    appendFact(facts, "Evidence model", friendlyStatus(metrics.status || geminiTrace.status || "Not invoked"));
    appendFact(facts, "Calls", `${Number(metrics.calls || 0)}${metrics.retries ? ` / ${metrics.retries} retry` : ""}`);
    appendFact(facts, "Pages reviewed", String(metrics.pages || 0));
    appendFact(facts, "Token usage", Number(usage).toLocaleString("en-IN"));
    appendFact(facts, "Model", geminiTrace.model || "Not recorded");
    byId("ai-cost-note").textContent = usage
      ? "Token counts are shown as the cost proxy. Provider billing is not available in this local record."
      : "No model usage was recorded for this claim.";
  }

  function renderClaim(claim) {
    const result = claim.result || {};
    const [statusText, statusKind] = statePresentation(claim);
    const badge = byId("state-badge");
    badge.textContent = statusText;
    badge.className = `state-badge state-badge--${statusKind}`;
    byId("breadcrumb-id").textContent = shortId(claim.id);
    byId("claim-title").textContent = `Claim ${shortId(claim.id)}`;
    byId("claim-subtitle").textContent = `${titleCase(claim.request.claim_category)} / ${claim.request.member_id} / ${formatMoney(claim.request.claimed_amount)} claimed`;
    const active = ["QUEUED", "PROCESSING"].includes(claim.state);
    byId("loading-state").hidden = !active;
    byId("failure-state").hidden = claim.state !== "PROCESSING_FAILED";
    byId("result-content").hidden = active || claim.state === "PROCESSING_FAILED";
    if (claim.state === "PROCESSING_FAILED") byId("failure-message").textContent = claim.error_message || "The review could not finish. You can retry the claim.";
    if (active || claim.state === "PROCESSING_FAILED") return;

    const isCorrection = ["DOCUMENT_CORRECTION_REQUIRED", "NEEDS_CORRECTION"].includes(claim.state);
    byId("correction-panel").hidden = !isCorrection;
    byId("decision-heading").textContent = isCorrection ? "Awaiting documents" : titleCase(result.decision || "Review needed");
    const reasons = Array.isArray(result.reasons) ? result.reasons : (result.reason ? [result.reason] : []);
    const primaryReason = reasons[0];
    byId("decision-reason").textContent = isCorrection ? "The document check stopped this claim before adjudication." : (typeof primaryReason === "string" ? primaryReason : primaryReason?.message || "See the decision trace below.");
    const amountPendingReview = result.decision === "MANUAL_REVIEW";
    byId("amount-label").textContent = amountPendingReview ? "Amount pending review" : "Approved amount";
    byId("approved-amount").textContent = result.approved_amount_paise === null || result.approved_amount === null || isCorrection || amountPendingReview ? "Pending" : formatMoney(result.approved_amount_paise !== undefined ? Number(result.approved_amount_paise) / 100 : result.approved_amount);
    byId("confidence-value").textContent = Number.isFinite(Number(result.confidence_score)) && result.confidence_score !== null ? `Evidence quality score ${Math.round(Number(result.confidence_score) * 100)}%` : "No confidence score yet";

    const corrections = byId("correction-list");
    corrections.replaceChildren();
    (result.correction_requests || []).forEach((request) => corrections.append(make("li", "", typeof request === "string" ? request : request.message || humanValue(request))));
    renderTrace(result.trace);
    renderLedger(result.ledger);
    renderEvidence(result, claim.documents);
    renderEscalation(result);
    renderAiMetrics(result);
    const payable = result.approved_amount_paise;
    byId("ledger-total").hidden = payable === null || payable === undefined || amountPendingReview;
    if (payable !== null && payable !== undefined) byId("ledger-payable").textContent = formatMoney(Number(payable) / 100);

    const facts = byId("claim-facts");
    facts.replaceChildren();
    appendFact(facts, "Member", claim.request.member_id);
    appendFact(facts, "Category", titleCase(claim.request.claim_category));
    appendFact(facts, "Treatment date", claim.request.treatment_date);
    appendFact(facts, "Claimed", formatMoney(claim.request.claimed_amount));
    const annualLimit = (claim.result?.trace || []).find((entry) => entry.rule_id === "annual_opd_limit")?.evidence;
    appendFact(facts, "YTD approved", annualLimit?.ytd_claims_amount === undefined ? "Unknown" : formatMoney(annualLimit.ytd_claims_amount));
    appendFact(facts, "Policy", claim.request.policy_id || "Not recorded");

    const documents = byId("document-list");
    documents.replaceChildren();
    (claim.documents || []).forEach((document) => {
      const item = make("li", "", document.original_name);
      item.append(make("span", "", `${(document.size_bytes / 1024 / 1024).toFixed(1)} MB / ${document.media_type}`));
      documents.append(item);
    });
    const events = byId("event-list");
    events.replaceChildren();
    (claim.events || []).forEach((event) => {
      const item = make("li", "", friendlyLabel(event.stage));
      item.append(make("span", "", new Date(event.occurred_at).toLocaleString("en-IN")));
      events.append(item);
    });
  }

  function setupClaim() {
    const claimId = document.body.dataset.claimId;
    let timer;
    async function refresh() {
      try {
        const response = await fetch(`/api/claims/${encodeURIComponent(claimId)}`, { cache: "no-store" });
        if (!response.ok) throw new Error(await messageFromError(response));
        const claim = await response.json();
        renderClaim(claim);
        if (["QUEUED", "PROCESSING"].includes(claim.state)) timer = window.setTimeout(refresh, 1600);
      } catch (cause) {
        byId("loading-state").hidden = true;
        byId("failure-state").hidden = false;
        byId("failure-message").textContent = cause.message || "The claim could not be loaded. Refresh to try again.";
        byId("retry-button").hidden = true;
        byId("state-badge").textContent = "Unavailable";
        byId("state-badge").className = "state-badge state-badge--failed";
      }
    }
    byId("retry-button").addEventListener("click", async () => {
      const button = byId("retry-button");
      button.disabled = true;
      button.textContent = "Retrying…";
      try {
        const response = await fetch(`/api/claims/${encodeURIComponent(claimId)}/retry`, { method: "POST" });
        if (!response.ok) throw new Error(await messageFromError(response));
        button.textContent = "Retry review";
        button.disabled = false;
        refresh();
      } catch (cause) {
        byId("failure-message").textContent = cause.message || "Retry failed. Try again.";
        button.textContent = "Retry review";
        button.disabled = false;
      }
    });
    window.addEventListener("pagehide", () => window.clearTimeout(timer));
    refresh();
  }

  if (page === "intake") setupIntake();
  if (page === "claim") setupClaim();
})();
