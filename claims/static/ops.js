(function () {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = { claims: [], filter: "attention", query: "", selectedId: null, request: null };

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function titleCase(value) {
    return String(value || "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  const FRIENDLY_LABELS = {
    actual_type: "Detected document type", annual_opd_limit: "Annual outpatient benefit limit",
    approved_amount: "Approved amount", approved_amount_paise: "Approved amount",
    bill_arithmetic_conflict: "Bill total does not match its line items",
    candidate_evidence: "Model-supported evidence", claimed_amount: "Claimed amount",
    confidence: "Extraction confidence", co_pay: "Member co-pay",
    consultation: "Consultation coverage", date: "Treatment date",
    decision_validation: "Decision consistency check", diagnosis: "Diagnosis",
    document_evidence: "Document evidence", document_extraction: "Document extraction",
    document_gate: "Required document check", document_type: "Document type",
    duplicate_check: "Duplicate bill check", evidence: "Supporting facts",
    extracted_facts: "Facts extracted from documents", extraction_source: "Extraction method",
    file_id: "Document reference", file_name: "Document name", fields: "Extracted facts",
    gemini_evidence: "Model evidence review", hospital_name: "Hospital or clinic",
    line_items: "Bill line items", manual_review: "Manual review", matching_claim_count: "Similar claims found",
    matching_claim_ids: "Related claim references", member_id: "Member identifier",
    missing_document: "Required document is missing", patient_name: "Patient name",
    policy_ref: "Policy clause", previous_bill_hash: "Previously submitted bill match",
    quality: "Document quality", required_type: "Required document", root_cause: "Primary review reason",
    source: "Evidence source", sources: "Evidence sources", stage: "Review step",
    status: "Check result", test_name: "Test or procedure", total: "Document total",
    total_paise: "Document total", waiting_period: "Waiting period check", ytd_claims_amount: "Approved this policy year",
  };

  const FRIENDLY_STATUSES = {
    ABSTAINED: "Model abstained", APPROVED: "Approved", BLOCKED: "Stopped",
    CANDIDATES_APPLIED: "Evidence verified", CANDIDATES_VALIDATED: "Evidence verified",
    DEGRADED: "Review needed", DISABLED: "Off", FAIL: "Failed", FAILED: "Failed", FLAG: "Review needed",
    NOT_EVALUATED: "Not evaluated", NOT_NEEDED: "No model call needed", OK: "Passed", PASS: "Passed",
    PASSED: "Passed", RECORDED: "Recorded", REJECTED: "Rejected", SKIPPED: "Not required", WARNING: "Review needed",
  };

  function friendlyLabel(value) {
    const key = String(value || "").toLowerCase();
    return FRIENDLY_LABELS[key] || titleCase(key).replace(" Id", " identifier").replace(" Paise", "");
  }

  function friendlyStatus(value) {
    const key = String(value || "").toUpperCase();
    return FRIENDLY_STATUSES[key] || titleCase(key);
  }

  function readablePolicyReference(value) {
    const segments = String(value || "").split(/[.>\[\]\/]+/).filter(Boolean);
    return segments.length ? friendlyLabel(segments[segments.length - 1]) : "Policy terms";
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

  function appendFactValue(parent, value, keyName) {
    if (Array.isArray(value)) {
      if (!value.length) {
        parent.textContent = "None recorded";
        return;
      }
      const list = make("ul", "ops-fact-list");
      value.forEach((item) => {
        const listItem = make("li");
        appendFactValue(listItem, item, keyName);
        list.append(listItem);
      });
      parent.append(list);
      return;
    }
    if (value && typeof value === "object") {
      const facts = make("dl", "ops-fact-grid ops-fact-grid--nested");
      Object.entries(value).forEach(([key, item]) => {
        const row = make("div");
        const valueNode = make("dd");
        appendFactValue(valueNode, item, key);
        row.append(make("dt", "", friendlyLabel(key)), valueNode);
        facts.append(row);
      });
      parent.append(facts);
      return;
    }
    parent.textContent = humanValue(value, keyName);
  }

  function shortId(id) { return String(id || "").slice(0, 8).toUpperCase(); }

  function formatMoney(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "Pending";
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(amount);
  }

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Time unavailable";
    return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function claimStatus(claim) {
    const decision = claim.decision || claim.result?.decision;
    if (["DOCUMENT_CORRECTION_REQUIRED", "NEEDS_CORRECTION"].includes(claim.state)) return { label: "Document correction", kind: "correction" };
    if (claim.state === "PROCESSING_FAILED") return { label: "Processing failed", kind: "failed" };
    if (["QUEUED", "PROCESSING"].includes(claim.state)) return { label: titleCase(claim.state), kind: "processing" };
    const labels = {
      APPROVED: ["Approved", "approved"], PARTIAL: ["Partially approved", "partial"],
      REJECTED: ["Rejected", "rejected"], MANUAL_REVIEW: ["Manual review", "review"],
    };
    const match = labels[decision];
    return match ? { label: match[0], kind: match[1] } : { label: titleCase(claim.state || "Pending"), kind: "processing" };
  }

  function needsAttention(claim) {
    return claim.decision === "MANUAL_REVIEW" || ["MANUAL_REVIEW", "DOCUMENT_CORRECTION_REQUIRED", "NEEDS_CORRECTION", "PROCESSING_FAILED"].includes(claim.state);
  }

  function isDecided(claim) {
    return ["APPROVED", "PARTIAL", "REJECTED"].includes(claim.decision);
  }

  function aiLabel(result) {
    const gemini = (result?.document_metrics || result?.metrics || {}).gemini || {};
    const status = String(gemini.status || "").toUpperCase();
    if (status === "NOT_NEEDED") return "No model call needed";
    if (status === "DISABLED") return "Model review off";
    if (["CANDIDATES_VALIDATED", "CANDIDATES_APPLIED"].includes(status)) return "Evidence assisted";
    if (status === "ABSTAINED") return "Model abstained";
    if (Number(gemini.calls || 0) > 0) return "Evidence model called";
    return "Not invoked";
  }

  function reasonText(reason) {
    if (typeof reason === "string") return reason;
    if (!reason || typeof reason !== "object") return "A reviewer must inspect the complete record.";
    const code = reason.code ? `${friendlyLabel(reason.code)}: ` : "";
    return `${code}${reason.message || reason.reason || humanValue(reason)}`;
  }

  function showError(message) {
    byId("ops-error-message").textContent = message || "Claims could not be loaded. Try again.";
    byId("ops-error").hidden = false;
  }

  function updateMetrics() {
    byId("metric-review").textContent = state.claims.filter((claim) => claim.decision === "MANUAL_REVIEW" || claim.state === "MANUAL_REVIEW").length;
    byId("metric-correction").textContent = state.claims.filter((claim) => ["DOCUMENT_CORRECTION_REQUIRED", "NEEDS_CORRECTION"].includes(claim.state)).length;
    byId("metric-failed").textContent = state.claims.filter((claim) => claim.state === "PROCESSING_FAILED").length;
    byId("metric-total").textContent = state.claims.length;
  }

  function visibleClaims() {
    const query = state.query.trim().toLowerCase();
    return state.claims.filter((claim) => {
      if (state.filter === "attention" && !needsAttention(claim)) return false;
      if (state.filter === "decided" && !isDecided(claim)) return false;
      if (!query) return true;
      return [claim.id, claim.request?.member_id, claim.request?.claim_category, claim.decision, claim.state]
        .some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function emptyWorklist(list) {
    const empty = make("div", "worklist-empty");
    empty.append(make("strong", "", state.query ? "No matching claims" : state.filter === "attention" ? "No claims need attention" : "No claims in this view"));
    empty.append(make("p", "", state.query ? "Try a claim ID, member ID, or category." : "New submissions will appear here automatically."));
    list.append(empty);
  }

  function renderList() {
    const list = byId("ops-list");
    const claims = visibleClaims();
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");
    byId("worklist-count").textContent = `${claims.length} ${claims.length === 1 ? "record" : "records"}`;
    if (!claims.length) {
      emptyWorklist(list);
      if (!state.selectedId) showEmptyDetail();
      return;
    }
    claims.forEach((claim) => {
      const status = claimStatus(claim);
      const row = make("button", "worklist-row");
      row.type = "button";
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(claim.id === state.selectedId));
      row.dataset.claimId = claim.id;
      if (claim.id === state.selectedId) row.classList.add("worklist-row--selected");
      const top = make("span", "worklist-row__top");
      top.append(make("strong", "worklist-row__id", shortId(claim.id)));
      top.append(make("span", `state-badge state-badge--${status.kind}`, status.label));
      const title = make("span", "worklist-row__title", titleCase(claim.request?.claim_category || "Claim"));
      const meta = make("span", "worklist-row__meta");
      meta.append(make("span", "", claim.request?.member_id || "Member not recorded"));
      meta.append(make("span", "", formatMoney(claim.request?.claimed_amount)));
      const time = make("span", "worklist-row__time", formatTime(claim.created_at));
      row.append(top, title, meta, time);
      row.addEventListener("click", () => selectClaim(claim.id));
      list.append(row);
    });
  }

  function showEmptyDetail() {
    byId("ops-detail-loading").hidden = true;
    byId("ops-detail-content").hidden = true;
    byId("ops-detail-empty").hidden = false;
  }

  function showDetailLoading() {
    byId("ops-detail-empty").hidden = true;
    byId("ops-detail-content").hidden = true;
    byId("ops-detail-loading").hidden = false;
  }

  function renderEvidence(result, documents) {
    const list = byId("ops-evidence-list");
    list.replaceChildren();
    const trace = Array.isArray(result.trace) ? result.trace : [];
    const documentTrace = trace.find((entry) => entry.stage === "document_evidence");
    const records = Array.isArray(documentTrace?.evidence) ? documentTrace.evidence : [];
    const geminiTrace = trace.find((entry) => entry.stage === "gemini_evidence");
    const metrics = result.document_metrics || result.metrics || {};

    const ai = make("article", "ops-evidence-card ops-evidence-card--ai");
    const aiHead = make("div", "ops-evidence-card__head");
    aiHead.append(make("strong", "", "Evidence processing"), make("span", "", aiLabel(result)));
    ai.append(aiHead);
    const tokenCount = metrics.gemini?.total_tokens ?? (Number(metrics.gemini?.input_tokens || 0) + Number(metrics.gemini?.output_tokens || 0));
    const metricFacts = make("dl", "ops-ai-metrics");
    [
      ["Document OCR", `${Number(metrics.sarvam_digitise_calls || 0)} calls / ${Number(metrics.sarvam_digitise_pages || 0)} pages`],
      ["Structured extraction", `${Number(metrics.sarvam_extract_calls || 0)} calls / ${Number(metrics.sarvam_extract_pages || 0)} pages`],
      ["Evidence model", `${Number(metrics.gemini?.calls || 0)} calls / ${Number(metrics.gemini?.pages || 0)} pages`],
      ["Model retries", String(Number(metrics.gemini?.retries || 0))],
      ["Model tokens", Number(tokenCount || 0).toLocaleString("en-IN")],
      ["Model name", geminiTrace?.model || "Not recorded"],
    ].forEach(([label, value]) => {
      const row = make("div");
      row.append(make("dt", "", label), make("dd", "", value));
      metricFacts.append(row);
    });
    ai.append(metricFacts);
    if (geminiTrace) {
      const modelNotes = {
        CANDIDATES_APPLIED: "Model-suggested facts were verified against the documents and then passed into policy checks.",
        CANDIDATES_VALIDATED: "Model-suggested facts were verified against the documents before policy evaluation.",
        NOT_NEEDED: "Local extraction had enough evidence, so no model call was needed.",
        ABSTAINED: "The model did not provide usable evidence. The record retained local evidence and fail-safe review rules.",
      };
      ai.append(make("p", "ops-evidence-card__note", modelNotes[String(geminiTrace.status || "").toUpperCase()] || "Model activity is recorded for evidence support only. Policy checks produced the final decision."));
    }
    list.append(ai);

    records.forEach((record, index) => {
      const card = make("article", "ops-evidence-card");
      const head = make("div", "ops-evidence-card__head");
      const file = (documents || [])[index];
      head.append(make("strong", "", file?.original_name || (record.file_id ? humanValue(record.file_id, "file_id") : `Document ${index + 1}`)));
      head.append(make("span", "", [titleCase(record.document_type), titleCase(record.quality)].filter(Boolean).join(" / ")));
      card.append(head);
      const facts = make("dl", "ops-fact-grid");
      Object.entries(record.fields || {}).forEach(([key, value]) => {
        const item = make("div");
        const valueNode = make("dd");
        appendFactValue(valueNode, value, key);
        item.append(make("dt", "", friendlyLabel(key)), valueNode);
        facts.append(item);
      });
      if (facts.children.length) card.append(facts);
      (record.sources || []).filter((source) => source.snippet).slice(0, 4).forEach((source) => {
        const quote = make("blockquote", "ops-source-quote", source.snippet);
        quote.append(make("cite", "", `${friendlyLabel(source.field)} / Page ${source.page || "unknown"} / ${friendlyLabel(source.source)}`));
        card.append(quote);
      });
      list.append(card);
    });

    const count = records.length;
    byId("ops-evidence-count").textContent = `${count} extracted ${count === 1 ? "document" : "documents"}`;
  }

  function traceKind(status) {
    const value = String(status || "").toUpperCase();
    if (["PASS", "PASSED", "OK", "APPROVED", "RECORDED", "CANDIDATES_APPLIED"].includes(value)) return "pass";
    if (["FAIL", "FAILED", "BLOCKED", "REJECTED"].includes(value)) return "fail";
    if (["FLAG", "WARNING", "NOT_EVALUATED", "DEGRADED", "ASSUMPTION", "ABSTAINED"].includes(value) || value.startsWith("SKIPPED")) return "warning";
    return "neutral";
  }

  function renderTrace(trace) {
    const list = byId("ops-trace-list");
    list.replaceChildren();
    if (!Array.isArray(trace) || !trace.length) {
      list.append(make("li", "ops-inline-empty", "No decision trace was recorded."));
      return;
    }
    trace.forEach((entry, index) => {
      const item = make("li", "ops-trace-entry");
      item.dataset.kind = traceKind(entry.status || entry.outcome);
      const body = make("div");
      body.append(make("strong", "", friendlyLabel(entry.rule_id || entry.stage || `Check ${index + 1}`)));
      const reason = entry.reason || entry.message || entry.explanation || entry.details;
      if (reason) body.append(make("p", "", humanValue(reason)));
      const evidence = entry.evidence;
      if (evidence && (typeof evidence !== "object" || Object.keys(evidence).length)) {
        const details = make("details");
        details.append(make("summary", "", "Inspect evidence"));
        const facts = make("dl", "ops-structured-facts");
        Object.entries(typeof evidence === "object" && !Array.isArray(evidence) ? evidence : { fact: evidence }).forEach(([key, value]) => {
          const row = make("div");
          const valueNode = make("dd");
          appendFactValue(valueNode, value, key);
          row.append(make("dt", "", friendlyLabel(key)), valueNode);
          facts.append(row);
        });
        details.append(facts);
        body.append(details);
      }
      item.append(make("span", "ops-trace-entry__index", String(index + 1).padStart(2, "0")), body, make("span", "ops-trace-entry__status", friendlyStatus(entry.status || entry.outcome || "Recorded")));
      list.append(item);
    });
  }

  function renderLedger(result) {
    const list = byId("ops-ledger-list");
    list.replaceChildren();
    const ledger = Array.isArray(result.ledger) ? result.ledger : [];
    if (!ledger.length) {
      list.append(make("p", "ops-inline-empty", result.decision === "MANUAL_REVIEW" ? "The payable amount is pending human review." : "No payable amount calculation was recorded."));
      return;
    }
    ledger.forEach((entry) => {
      const row = make("div", "ops-ledger-row");
      const body = make("div");
      body.append(make("strong", "", entry.description || entry.label || friendlyLabel(entry.rule_id || entry.stage || "Adjustment")));
      const context = [entry.status && friendlyStatus(entry.status), entry.reason_code && friendlyLabel(entry.reason_code), entry.source_document && `Source: ${entry.source_document}`, entry.policy_ref && `Policy: ${readablePolicyReference(entry.policy_ref)}`].filter(Boolean).join(" / ");
      if (context) body.append(make("span", "", context));
      const paise = entry.amount_paise ?? entry.value_paise ?? entry.approved_amount_paise;
      const amount = paise !== undefined && paise !== null ? Number(paise) / 100 : entry.amount ?? entry.value;
      row.append(body, make("strong", "ops-ledger-row__amount", formatMoney(amount)));
      list.append(row);
    });
    const total = make("div", "ops-ledger-total");
    const approved = result.approved_amount_paise !== undefined && result.approved_amount_paise !== null ? Number(result.approved_amount_paise) / 100 : result.approved_amount;
    total.append(make("span", "", "Final approved amount"), make("strong", "", result.decision === "MANUAL_REVIEW" ? "Pending" : formatMoney(approved)));
    list.append(total);
  }

  function renderFilesAndEvents(claim) {
    const files = byId("ops-file-list");
    files.replaceChildren();
    (claim.documents || []).forEach((document) => {
      const item = make("li", "", document.original_name || "Unnamed document");
      const size = Number(document.size_bytes);
      item.append(make("span", "", `${Number.isFinite(size) ? `${(size / 1024 / 1024).toFixed(1)} MB` : "Size unavailable"} / ${document.media_type || "Type unavailable"}`));
      files.append(item);
    });
    if (!files.children.length) files.append(make("li", "ops-inline-empty", "No submitted files were recorded."));

    const events = byId("ops-event-list");
    events.replaceChildren();
    (claim.events || []).forEach((event) => {
      const item = make("li", "", friendlyLabel(event.stage || "Event"));
      item.append(make("span", "", formatTime(event.occurred_at)));
      if (event.detail && Object.keys(event.detail).length) {
        const details = make("details");
        const facts = make("dl", "ops-structured-facts");
        Object.entries(typeof event.detail === "object" ? event.detail : { detail: event.detail }).forEach(([key, value]) => {
          const row = make("div");
          row.append(make("dt", "", friendlyLabel(key)), make("dd", "", humanValue(value, key)));
          facts.append(row);
        });
        details.append(make("summary", "", "Event details"), facts);
        item.append(details);
      }
      events.append(item);
    });
    if (!events.children.length) events.append(make("li", "ops-inline-empty", "No processing events were recorded."));
    byId("ops-event-count").textContent = `${(claim.events || []).length} events`;
  }

  function renderDetail(claim) {
    const result = claim.result || {};
    const status = claimStatus(claim);
    byId("ops-detail-loading").hidden = true;
    byId("ops-detail-empty").hidden = true;
    byId("ops-detail-content").hidden = false;
    byId("ops-detail-id").textContent = `Claim ${shortId(claim.id)}`;
    byId("ops-detail-time").textContent = formatTime(claim.created_at);
    byId("ops-detail-heading").textContent = titleCase(claim.request?.claim_category || "Claim review");
    byId("ops-detail-subtitle").textContent = `${claim.request?.member_id || "Member not recorded"} / ${formatMoney(claim.request?.claimed_amount)} claimed / Treated ${claim.request?.treatment_date || "date not recorded"}`;
    const badge = byId("ops-detail-badge");
    badge.textContent = status.label;
    badge.className = `state-badge state-badge--${status.kind}`;

    const reasons = Array.isArray(result.reasons) ? result.reasons : result.reason ? [result.reason] : [];
    const escalation = byId("ops-escalation");
    const escalated = status.kind === "review" || status.kind === "correction" || status.kind === "failed";
    escalation.hidden = !escalated;
    if (escalated) {
      const heading = status.kind === "correction" ? "Member action required" : status.kind === "failed" ? "Processing recovery required" : "Human decision required";
      byId("ops-escalation-title").textContent = heading;
      byId("ops-escalation-reason").textContent = reasons.length ? reasons.map(reasonText).join(" ") : claim.error_message || "Inspect the decision trace and submitted evidence before continuing.";
    }

    byId("ops-decision").textContent = result.decision ? titleCase(result.decision) : "Not decided";
    const amount = result.approved_amount_paise !== undefined && result.approved_amount_paise !== null ? Number(result.approved_amount_paise) / 100 : result.approved_amount;
    byId("ops-approved").textContent = result.decision === "MANUAL_REVIEW" || result.decision == null ? "Pending" : formatMoney(amount);
    const score = Number(result.confidence_score);
    byId("ops-confidence").textContent = result.confidence_score !== null && result.confidence_score !== undefined && Number.isFinite(score) ? `${Math.round(score * 100)}%` : "Not scored";
    byId("ops-ai-status").textContent = aiLabel(result);
    byId("ops-decision-reason").textContent = reasons.length ? reasons.map(reasonText).join(" ") : result.decision ? "The recorded document evidence passed through the policy checks shown below." : "The claim has not reached a final coverage decision.";
    byId("ops-open-claim").href = `/claims/${encodeURIComponent(claim.id)}`;
    renderEvidence(result, claim.documents);
    renderTrace(result.trace);
    renderLedger(result);
    renderFilesAndEvents(claim);
    const action = byId("ops-review-action");
    action.hidden = claim.state !== "MANUAL_REVIEW";
    if (!action.hidden) {
      byId("ops-review-amount").value = "";
      byId("ops-review-reason-code").value = "";
      byId("ops-review-reason-text").value = "";
      byId("ops-review-evidence").value = "";
      byId("ops-review-error").hidden = true;
    }
  }

  async function selectClaim(id) {
    if (!id) return;
    state.selectedId = id;
    renderList();
    showDetailLoading();
    if (state.request) state.request.abort();
    state.request = new AbortController();
    try {
      const response = await fetch(`/api/claims/${encodeURIComponent(id)}`, { cache: "no-store", signal: state.request.signal });
      if (!response.ok) throw new Error("This claim could not be loaded.");
      const claim = await response.json();
      if (state.selectedId !== id) return;
      renderDetail(claim);
    } catch (error) {
      if (error.name === "AbortError") return;
      showEmptyDetail();
      showError(error.message);
    }
  }

  async function loadClaims() {
    byId("ops-error").hidden = true;
    byId("ops-list").setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/claims?limit=100", { cache: "no-store" });
      if (!response.ok) throw new Error("Claims could not be loaded. Try again.");
      state.claims = (await response.json()).claims || [];
      updateMetrics();
      const priority = state.claims.find(needsAttention) || state.claims[0];
      state.selectedId = priority?.id || null;
      renderList();
      if (priority) selectClaim(priority.id);
      else showEmptyDetail();
    } catch (error) {
      byId("ops-list").replaceChildren();
      byId("ops-list").setAttribute("aria-busy", "false");
      byId("worklist-count").textContent = "Unavailable";
      showEmptyDetail();
      showError(error.message);
    }
  }

  document.querySelectorAll(".filter-tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      document.querySelectorAll(".filter-tab").forEach((tab) => {
        const active = tab === button;
        tab.classList.toggle("filter-tab--active", active);
        tab.setAttribute("aria-pressed", String(active));
      });
      renderList();
    });
  });

  byId("ops-search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderList();
  });

  byId("ops-list").addEventListener("keydown", (event) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const rows = Array.from(byId("ops-list").querySelectorAll(".worklist-row"));
    if (!rows.length) return;
    event.preventDefault();
    const current = rows.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : event.key === "ArrowDown" ? Math.min(current + 1, rows.length - 1) : Math.max(current - 1, 0);
    rows[next].focus();
  });

  byId("ops-retry").addEventListener("click", loadClaims);
  byId("ops-review-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = byId("ops-review-error");
    error.hidden = true;
    if (!state.selectedId) return;
    const decision = byId("ops-review-decision").value;
    const approvedAmount = Number(byId("ops-review-amount").value);
    const reasonCode = byId("ops-review-reason-code").value.trim().toUpperCase();
    const reasonText = byId("ops-review-reason-text").value.trim();
    const evidenceSummary = byId("ops-review-evidence").value.trim();
    if (!Number.isFinite(approvedAmount) || approvedAmount < 0 || (decision !== "REJECTED" && approvedAmount <= 0)) {
      error.textContent = "Enter a valid approved amount for the selected decision.";
      error.hidden = false;
      return;
    }
    if (!/^[A-Z0-9_]{3,60}$/.test(reasonCode) || !reasonText || !evidenceSummary) {
      error.textContent = "Add a reason code, decision reason, and evidence summary.";
      error.hidden = false;
      return;
    }
    try {
      const response = await fetch(`/api/claims/${encodeURIComponent(state.selectedId)}/review-decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision, approved_amount: approvedAmount, reason_code: reasonCode,
          reason_text: reasonText, evidence_summary: evidenceSummary,
        }),
      });
      if (!response.ok) throw new Error(await response.json().then((body) => body.detail || "Decision could not be saved."));
      const claim = await response.json();
      state.claims = state.claims.map((item) => item.id === claim.id ? { ...item, state: claim.state, decision: claim.result?.decision } : item);
      updateMetrics();
      renderList();
      renderDetail(claim);
    } catch (cause) {
      error.textContent = cause.message || "Decision could not be saved.";
      error.hidden = false;
    }
  });
  loadClaims();
})();
