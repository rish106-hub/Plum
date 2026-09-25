# Claims processing: requirements and architecture research

Research date: 23 September 2026. This is a design brief for the supplied Plum AI Engineer assignment, not a description of Plum's private implementation.

## North star

Reduce the time and effort from document upload to a defensible claim outcome. Optimize four measurable quantities together: cost per correctly resolved claim, time to first useful feedback, manual review minutes, and harmful decision errors. A lower model bill is valuable only when it does not create more rework or incorrect decisions.

The assignment's 75,000 claims/year is about 205 claims/day on average. A tenfold increase is about 2,055/day. Average traffic alone does not justify a microservice fleet; burst handling, durable jobs, and clear ownership of state matter first.

## What is known about Plum

| Publicly documented product | What it does | Architectural lesson |
| --- | --- | --- |
| PolicyGPT | Conversational policy and coverage answers; Plum's launch post acknowledged possible errors. | Make policy understandable, but do not let conversational text authorize payment. |
| ClaimsLens | Uses AI/OCR to flag missing or unclear documents and mismatches before reimbursement submission; later product material mentions reading bills and checking totals. | Move repairable errors to the upload moment. |
| Claims Router | Detects insurer clarifications and stuck claims and prompts coordination. | Exception handling and status visibility can be more valuable than another model call. |
| Penny Drop and care team | Removes a bank-verification delay and keeps humans involved in difficult cases. | Optimize the whole claim journey, including handoffs. |

These are product behaviors supported by Plum's public material. Plum has not disclosed its OCR provider, LLM, queue, database, agent framework, fraud model, or exact adjudication rules. The shared ChatGPT conversation is a useful research lead; its proposed technical architecture is inference, not evidence of Plum's internal stack. Plum's claims metrics are company-reported and have different definitions across pages and dates (for example, automation, minimal human intervention, and no user conversation). They should not be treated as one metric.

Sources: [Plum claims experience](https://claims.plumhq.com/), [ClaimsLens release](https://www.plumhq.com/winter-release-2024), [Autumn 2025 update](https://www.plumhq.com/autumn-2025), [PolicyGPT launch](https://www.plumhq.com/blog/introducing-policygpt), [cashless claim roles](https://www.plumhq.com/blog/how-cashless-claims-work-group-health-insurance).

## What comparable systems teach

| System | Public architecture or capability | Why it works | Where it becomes costly or fragile | What to borrow |
| --- | --- | --- | --- | --- |
| [openIMIS claim module](https://github.com/openimis/openimis-be-claim_py) | Claims, attachments, item/service rows, review and feedback, explicit lifecycle. | Durable state and reviewer actions. | Full health-financing platform is too broad for this assignment. | Claim state machine and decision history. |
| [AWS event-driven claims sample](https://github.com/aws-samples/serverless-eda-insurance-claims-processing) | Separate document, claims, fraud, settlement, and notification domains connected by events. | Handles slow steps and retries independently. | Distributed deployment and event choreography add operations burden at small scale. | Queue and retry boundaries, not the whole service fleet. |
| [Microsoft content processing accelerator](https://github.com/microsoft/content-processing-solution-accelerator) | Asynchronous multi-document extraction, mapping, evaluation, and save stages. | Documents can finish independently before claim aggregation. | General-purpose platform needs customization for health policy decisions. | Per-document jobs and validated aggregation. |
| [Medi Assist MAtrix](https://mediassist.in/products/matrix/) | Document digitization, configurable policy/tariff rules, work allocation and fraud tools. | Couples structured rules with operational review. | Its historical data and integration footprint took years to build; vendor performance claims need independent validation. | Configurable rule versioning and reviewer allocation. |
| [Vitraya claims](https://www.vitraya.com/solutions/claims) | Document and medical extraction, policy/finance checks, fraud signals, evidence-bearing decisions. | Separates evidence, rules, and risk. | Many specialist agents can increase latency and disagreement if every claim traverses them. | Logical layers and clause/evidence output. |
| [InterPixels](https://interpixels.ai/) | API for document completeness, extraction, consistency checks, structured output and exception routing. | Narrow upstream product creates value without replacing payer core. | Still depends on correct policy checklists and calibrated field confidence. | Focused document API and field-level exceptions. |
| [ClaimGPT](https://github.com/dev-azhar/ClaimGPT) | India-oriented OCR, parsing, validation, coding, fraud and audit prototype. | Good decomposition of raw text, parsed fields, and checks. | Many models and services enlarge build and maintenance cost. | Audit schema; keep only assignment-critical functions. |

These references disclose features and design patterns, not verified production accuracy. See also [AWS insurance IDP stages](https://aws.amazon.com/blogs/machine-learning/part-1-intelligent-document-processing-with-aws-ai-services-in-the-insurance-industry/) and [AWS claims architecture](https://aws.amazon.com/blogs/industries/building-a-modern-event-driven-application-for-insurance-claims-processing-part-2/).

## First-principles flow

The work is a sequence of uncertainty reductions:

1. Is the claim and member input structurally valid?
2. Are the required evidence types present and readable?
3. What facts can be extracted, and where did each fact come from?
4. Do the documents agree on identity, dates, treatment and totals?
5. Which policy rules apply, and what amount follows from their ordered arithmetic?
6. Is there a specific risk or uncertainty requiring a person?

The architecture should pay for more computation only when the next result can change a decision or prevent costly rework. This is **decision-bound evidence acquisition**: determine the fields required by the claim category and applicable rules; take the cheapest reliable route for each field; request a deeper visual pass only for a material ambiguity. The idea is a design hypothesis to benchmark, not a claim of novelty or measured savings.

```text
Member upload
  -> local file/page/quality preflight
  -> native PDF text when available; Sarvam OCR for scans
  -> document type + required-document gate
  -> typed extraction with page/region evidence
  -> patient/date/amount reconciliation
  -> deterministic rules from versioned policy_terms.json
  -> simple fraud/anomaly signals
  -> decision or human review, with step trace
```

Keep a separate **claim state** (`AWAITING_DOCUMENTS`, `PROCESSING`, `MANUAL_REVIEW`, `FINAL`) and **decision** (`APPROVED`, `PARTIAL`, `REJECTED`, `MANUAL_REVIEW`, or null before adjudication). TC001-TC003 require `decision: null` and a specific correction request. A final claim decision must never be inferred from a model's prose.

## Minimum component contracts

| Component | Input | Output | Failure behavior |
| --- | --- | --- | --- |
| Intake | Claim metadata and file references | Claim ID, file hashes, initial state | Reject malformed inputs with field-specific error. |
| Document gate | Category's required-document matrix and detected types/quality | Accepted set or actionable correction list | Uncertain type/quality requests review or re-upload; no claim decision. |
| Extractor adapter | File/page, requested field schema, provider choice | Typed fields, confidence, source page/region, raw response reference | Timeout/invalid schema becomes an explicit error; retry or route to review. |
| Reconciler | Extracted fields across documents and member data | Matches, conflicts, unknowns | Material identity/total conflicts stop adjudication. |
| Rule evaluator | Normalized claim facts and versioned policy | Ordered rule results and financial ledger | Missing decisive evidence yields review; never invent a value. |
| Risk router | Claim history, quality/failure signals, rule outputs | Signals and routing recommendation | Fraud suspicion routes to review, never automatic accusation or rejection. |
| Decision composer | Rule results, risk signals, confidence inputs | Decision, amount, reasons and trace | Conflicting rules surface as ambiguity. |

All stages write a trace event with stage/version, status, evidence references, rule ID, input values, result, duration and any degradation. Store raw documents separately from redacted operational logs. Use integer paise or decimal arithmetic and preserve a line-by-line calculation ledger.

## MVP implementation shape

One application with an upload API, a small background job worker, a relational database (SQLite locally; PostgreSQL for deployment), object storage for documents, and a reviewer UI. This provides one codebase and one durable workflow. The worker is an internal boundary that can later become a separately scaled service. A queue is needed for slow OCR jobs and rate limiting; a large agent orchestration framework is not required for the stated flow.

The model work can be represented as bounded specialists: document classifier, field extractor, and ambiguity resolver. Their outputs are typed and checked. A deterministic reducer owns adjudication. This qualifies as meaningful component specialization while keeping the authority and bill predictable.

For the assignment, a fixture adapter can accept the structured `test_cases.json` documents; the actual upload path must classify and parse images/PDFs. These are two input adapters into the same normalized claim model. Do not treat fixture-only `actual_type`, `quality`, or `content` as trustworthy production fields.

## API choices and cost hypothesis

| Need | Candidate | Verified public pricing or constraint |
| --- | --- | --- |
| PDF with usable text | Local PDF parser/Docling | No usage-based API charge; still costs compute and engineering. |
| Indian-language scans and tables | [Sarvam Document AI](https://docs.sarvam.ai/api/api-guides-tutorials/document-intelligence/overview) | [₹0.50/page Digitise, ₹1/page Extract](https://www.sarvam.ai/api-pricing); asynchronous jobs, 10-page document limit. |
| Ambiguous visual fields | [Gemini 3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash) | [Paid standard $0.75/M input tokens and $3.75/M output tokens through 2026](https://ai.google.dev/gemini-api/docs/pricing); prices double from January 2027 per the current page. |
| Structured model output | [Sarvam Extract](https://docs.sarvam.ai/api-reference/doc-ai/job/extract) or [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) | Schema validity does not establish factual correctness; validate values against evidence and cross-document constraints. |

If every claim has three scan pages and all pages use Sarvam Digitise, direct digitisation is `75,000 x 3 x ₹0.50 = ₹112,500/year` at the published rate. If 20% of claims additionally send all three pages to Sarvam Extract, add `75,000 x 20% x 3 x ₹1 = ₹45,000/year`, for **₹157,500/year or ₹2.10/claim** before retries, taxes, storage, compute, and people. An Extract-only route on all three pages would be ₹225,000/year at the published rate. These are scenarios, not observed usage. Gemini costs should be computed from actual image/text token counts and calls; its tiny text-only bill must not be assumed for full-page vision inputs.

The true break-even equation is:

`annual benefit = avoided review minutes x loaded staff cost/minute + avoided rework and delay value - API/compute/storage cost - new exception-review cost - cost of incorrect decisions`.

For illustration only, saving two staff minutes on each of 75,000 claims frees 150,000 minutes (2,500 hours). Value that time using Plum's actual loaded staffing cost and the fraction that can be redeployed; do not equate time freed to cash savings automatically. Track first-pass completeness, touchless rate, reviewer minutes/claim, correction cycles, p50/p95 time to first feedback, p95 total turnaround, wrongful-decision rate, and cost per correct resolution. Vendor-reported automation percentages are not substitutes for these measurements.

## Assignment policy conflicts to resolve in the specification

1. The ₹5,000 global per-claim limit and ₹2,000 consultation sub-limit conflict with TC006's ₹8,000 dental approval and TC010's ₹3,240 consultation approval. A single consistent interpretation is not supplied.
2. TC007's missing pre-authorization and TC012's exclusion compete with amount limits; decision-reason precedence must be explicit.
3. Dental `requires_dental_report: true` conflicts with the category document matrix, which lists the report as optional, and with TC006's bill-only input.
4. Fixtures have 2024 treatment dates but no submission/as-of date; the 30-day deadline and policy end cannot be applied against today's date.
5. Pre-authorization evidence format, fraud scoring, confidence calibration, and financial rounding/order are unspecified.

Make these contradictions visible in the architecture and evaluation report. For the fixture harness, use an explicit documented compatibility interpretation based on the expected outcomes, without branching on case IDs. For a real insurer integration, get an authoritative policy clarification before automated payment decisions.

## Validation plan before tool or architecture lock-in

Create a small labelled document set covering printed and handwritten prescriptions, phone photos, hospital/pharmacy bills, lab reports, stamps, multi-page PDFs, and mixed Indian-language text. Compare local text extraction, Sarvam Digitise, Sarvam Extract, and targeted Gemini visual extraction on document-type accuracy, critical-field exact match, patient false-match rate, line-item totals, source evidence quality, p50/p95 latency, and rupees per correctly processed claim. Separately run all 12 assignment fixtures through the normalized decision pipeline. Select thresholds by the cost of false approval, false rejection, and unnecessary review; calibrate confidence on held-out examples rather than trusting a model's self-score.

Production health data requires a vendor data-processing and retention review before sending it to external APIs. [Google's pricing page](https://ai.google.dev/gemini-api/docs/pricing) distinguishes free-tier data use from paid-tier data use. Synthetic assignment documents avoid this issue during the prototype.

## Practical sequence

1. Agree on rule precedence and fixture compatibility assumptions; define typed claim, evidence, rule-result and trace contracts.
2. Build document gate, fixture adapter, deterministic rule evaluator and trace first. This reveals semantic conflicts before spending on OCR.
3. Add PDF/image extraction behind a provider interface; benchmark Sarvam against local PDF text and targeted Gemini on representative documents.
4. Add review UI and failure recovery; measure correct resolution, cost and latency end to end.
5. Expand only where data shows a bottleneck: queues/workers for burst load, insurer status adapters, fraud models after labelled history, and broader standards such as FHIR/HCX when an integration requires them.
