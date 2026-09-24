# Bounded AI evidence resolution

## Authority and purpose

Gemini is an optional document evidence resolver. It can propose a document type or a value that the local parser could not confidently obtain. It cannot decide coverage, interpret a policy clause, change claim intake fields, or set an approved/payable amount. The caller must pass only validated evidence candidates to the deterministic claim evaluator; that evaluator recomputes limits, co-pay, discounts, and the final outcome from `policy_terms.json`.

This boundary follows an evidence-first claims flow: validate intake, confirm the evidence set, extract facts with page-level provenance, reconcile facts, apply versioned deterministic rules, then route only unresolved exceptions. Structured JSON constrains response shape; it does not prove factual accuracy. Every proposed fact is independently checked against OCR text, page bounds, identity roster, date parsing, positive integer paise, and bill arithmetic before it is returned as a candidate.

## Explicit opt-in

The resolver is disabled unless both `GEMINI_EVIDENCE_REVIEW_ENABLED=true` and `GEMINI_API_KEY` are set. `GEMINI_MODEL` selects the model and defaults to `gemini-3.8-flash`. A key by itself never enables external document transmission. Leave the toggle false for ordinary local use; turn it on only for a controlled synthetic test. Do not enable it for uploaded health documents unless the data-processing/retention review and operator authorization are complete.

## Exact trigger and route

`claims.ai_review.build_trigger` is deterministic and pure. It returns no trigger for clear documents, so the caller makes zero Gemini calls for claims whose required facts are already present and reconciled. It asks for a single grouped evidence pass only when one or more uploaded documents:

- have `UNKNOWN` type;
- lack a rule-relevant field for their detected type (patient name/diagnosis for prescriptions, total/line items for bills, patient/date/test name for lab reports); or
- have a bill total that does not equal the extracted line-item sum.

The trigger immediately abstains when known patient names disagree, a name fails the supplied member/dependent allowlist, source OCR text is absent, or an evidence field cannot be tied to a page using local text cues. It does not ask Gemini to resolve a roster mismatch. An absent required upload also cannot trigger Gemini because there are no bytes to inspect; the member must upload it.

## Data sent and cost caps

For each triggered file, the request includes only the fields to resolve, file ID, current document type, and short OCR lines related to those fields. It does not include the full OCR transcript or member ID, claim category, submitted amount, policy, claim history, unrelated files, or stored trace. The visual input contains only the locally selected page(s): a PDF page is rasterized locally to PNG; a one-page image is passed as-is or reduced to a bounded JPEG when it exceeds the page-byte cap. If local OCR cannot identify the page, the resolver abstains instead of sending the whole document.

Hard limits per claim call:

- 3 selected files;
- 4 selected pages total; the source document is already limited to 10 pages at intake;
- 1,200 characters of relevant OCR snippets per selected page;
- 5 MiB per rendered image page;
- 4,096 generated output tokens;
- 30 second transport timeout;
- explicit `LOW` thinking level for constrained field extraction;
- one additional attempt only for a recognized transient timeout, connection failure, or HTTP 408/429/5xx. SDK retries are disabled.

Metrics retain call/retry counts, selected files/pages, and provider input/output token counts so a cost can be calculated against the model's current published rate. No raw OCR, page image, API key, or provider exception body is put in the trace. The application should compare cost per correctly resolved claim, not only per-call token price.

## Response contract

The schema has exactly two top-level fields: `abstain` and `documents`. Each document can return only its file ID, document type, patient name, date, diagnosis, provider name, test name, total in integer paise, and line items (description and integer paise amount). Every candidate field has a page number and exact source quote; every line item has its own page and quote. There are no decision, policy, member-ID, approved-amount, payable-amount, or override fields in the schema. Empty strings, zero totals, and empty line-item lists mean no candidate.

The implementation validates that the response contains only the expected schema, references only requested files/fields, cites an in-range page, and quotes text found on that exact OCR page. Dates must parse to ISO `YYYY-MM-DD` and be supported by the cited date text. Amounts must be positive integer paise and occur in the cited quote; a total quote must include a total/amount label. A candidate patient name requires an explicit allowlist and exact normalized match. If both bill total and line items are returned, their integer-paise sum must match exactly. Any unsupported value, identity disagreement, line arithmetic conflict, invalid JSON/schema, or model abstention drops all candidates for that call and returns `ABSTAINED`.

Candidates are evidence corrections only. A caller may update the normalized document evidence after validating them, preserving the source quote and `gemini_candidate` provenance. It must never mutate the original request's member, claim category, treatment date, submitted claim amount, or policy. A clear bill total that differs from the user's entered claim amount should lead to a member correction request; Gemini cannot silently rewrite either value.

## Escalation outcomes

| Result | Caller action |
| --- | --- |
| `NOT_NEEDED` | Continue to deterministic checks; no model cost. |
| `CANDIDATES_VALIDATED` | Apply only the returned evidence fields, keep provenance, rerun deterministic validation and policy rules. |
| `ABSTAINED` for source, quality, identity, schema, arithmetic, or provider issue | Keep the original evidence unchanged; request a clearer/correct document when member-actionable, otherwise send to an operator. |

Missing/incorrect document type can only be cleared if the quoted heading is present in locally extracted page text and all required document checks pass again. Duplicate/fraud signals, member identity mismatch, policy contradictions, or ambiguous exclusions remain human work; an LLM cannot resolve them by narrative judgment. Provider failure never becomes approval/rejection.

## Claim-processing integration

`claims.web.process_claim` runs local/Sarvam extraction first, then removes the per-page OCR hand-off from the inspection before anything is persisted. The page text exists only in worker memory for `resolve_evidence`; file bytes are limited to the upload set and used to rasterize only selected pages. The worker supplies the covered employee and dependent names from policy for identity validation. It invokes the resolver only when the explicit opt-in is enabled; with opt-out, the recorded Gemini metrics show disabled and zero calls.

After a validated candidate, `claims.documents.apply_evidence_candidates` makes a deep copy of normalized document evidence, adds `gemini_candidate` quote/page provenance, and leaves the original member ID, claim category, treatment date, submitted amount and policy untouched. It then calls `revalidate_documents` to rebuild the evidence-dependent gates. The usual deterministic `claims.core.evaluate_claim` runs only when that gate is clear and remains the sole authority for coverage and amounts.

An arithmetic conflict is itself a blocking document issue even when the AI resolver abstains, returns invalid evidence, times out, or fails. The unchanged bill cannot proceed to policy evaluation. Extraction-provider outages route to manual review; member-actionable missing or conflicting evidence routes to document correction. If a material trigger somehow has no associated gate issue, integration must add a blocking issue before evaluation rather than treating abstention as clearance.

## Measurement before reducing human review

Use a labelled synthetic set first: clear digital PDFs, printed and handwritten prescriptions, scanned/photo bills, cropped or stamped documents, multi-page documents, conflicting names, and conflicting totals. Compare against the human-labelled field value and page quote. Measure per-field exact match, wrong-member false match, amount/line arithmetic accuracy, abstention rate, downstream correction rate, unnecessary human review, false approval/rejection, p50/p95 latency, and cost per correctly resolved claim. Confidence returned by a model is not used as a probability or auto-pay threshold.

Keep the feature opt-in until held-out evaluation demonstrates that validated candidates reduce human review without increasing harmful errors. Calibrate per-field thresholds from labelled results. Keep policy interpretations with conflicting source terms out of model resolution; the policy owner must clarify them.

## Developer contract

`resolve_evidence(documents, files_by_id, ocr_text_by_file_id, allowed_patient_names=..., transport=None)` returns `{status,candidates,trace,metrics}`. OCR page text must be supplied as a list with one item per original page, or a form-feed-separated string. The function does not modify the input mappings. `GoogleGenAITransport` is constructed lazily and reads only `GEMINI_API_KEY`; tests use a fake transport and no live API.

Official API references: [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/), [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output), and [Gemini document processing](https://ai.google.dev/gemini-api/docs/generate-content/document-processing).
