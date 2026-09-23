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
    if (!Number.isFinite(amount)) return "—";
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(amount);
  }

  function shortId(id) { return String(id || "").slice(0, 8).toUpperCase(); }

  function titleCase(value) {
    return String(value || "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
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
        row.append(make("span", "", titleCase(claim.decision || claim.state)));
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
      body.append(make("p", "trace-entry__title", titleCase(entry.rule_id || entry.stage || `Check ${index + 1}`)));
      const reason = entry.reason || entry.message || entry.explanation || entry.details || entry.output;
      if (reason && (!Array.isArray(reason) || reason.length)) body.append(make("p", "trace-entry__reason", typeof reason === "string" ? reason : JSON.stringify(reason)));
      const extra = Object.fromEntries(Object.entries(entry).filter(([key]) => !["rule_id", "stage", "status", "outcome", "reason", "message", "explanation", "details", "output"].includes(key)));
      if (Object.keys(extra).length) {
        const detail = make("details");
        detail.append(make("summary", "", "View evidence"));
        detail.append(make("pre", "", JSON.stringify(extra, null, 2)));
        body.append(detail);
      }
      item.append(body);
      item.append(make("span", "trace-entry__status", titleCase(status)));
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
      row.append(make("span", "", entry.description || entry.label || entry.rule_id || entry.stage || "Adjustment"));
      const paise = entry.amount_paise ?? entry.value_paise ?? entry.approved_amount_paise;
      const rupees = paise !== undefined && paise !== null ? Number(paise) / 100 : (entry.amount ?? entry.value);
      row.append(make("strong", "", formatMoney(rupees)));
      list.append(row);
    });
  }

  function renderClaim(claim) {
    const result = claim.result || {};
    const [statusText, statusKind] = statePresentation(claim);
    const badge = byId("state-badge");
    badge.textContent = statusText;
    badge.className = `state-badge state-badge--${statusKind}`;
    byId("breadcrumb-id").textContent = shortId(claim.id);
    byId("claim-title").textContent = `Claim ${shortId(claim.id)}`;
    byId("claim-subtitle").textContent = `${titleCase(claim.request.claim_category)} · ${claim.request.member_id} · ${formatMoney(claim.request.claimed_amount)} claimed`;
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
    byId("amount-label").textContent = "Approved amount";
    byId("approved-amount").textContent = result.approved_amount_paise === null || result.approved_amount === null || isCorrection ? "—" : formatMoney(result.approved_amount_paise !== undefined ? Number(result.approved_amount_paise) / 100 : result.approved_amount);
    byId("confidence-value").textContent = Number.isFinite(Number(result.confidence_score)) && result.confidence_score !== null ? `Evidence quality score ${Math.round(Number(result.confidence_score) * 100)}%` : "No confidence score yet";

    const corrections = byId("correction-list");
    corrections.replaceChildren();
    (result.correction_requests || []).forEach((request) => corrections.append(make("li", "", typeof request === "string" ? request : request.message || JSON.stringify(request))));
    renderTrace(result.trace);
    renderLedger(result.ledger);

    const facts = byId("claim-facts");
    facts.replaceChildren();
    appendFact(facts, "Member", claim.request.member_id);
    appendFact(facts, "Category", titleCase(claim.request.claim_category));
    appendFact(facts, "Treatment date", claim.request.treatment_date);
    appendFact(facts, "Claimed", formatMoney(claim.request.claimed_amount));
    appendFact(facts, "OPD already reimbursed", claim.request.ytd_claims_amount === undefined ? "Unknown" : formatMoney(claim.request.ytd_claims_amount));
    appendFact(facts, "Policy", claim.request.policy_id || "—");

    const documents = byId("document-list");
    documents.replaceChildren();
    (claim.documents || []).forEach((document) => {
      const item = make("li", "", document.original_name);
      item.append(make("span", "", `${(document.size_bytes / 1024 / 1024).toFixed(1)} MB · ${document.media_type}`));
      documents.append(item);
    });
    const events = byId("event-list");
    events.replaceChildren();
    (claim.events || []).forEach((event) => {
      const item = make("li", "", titleCase(event.stage));
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
