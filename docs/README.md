# Documentation

This directory groups supporting material by audience and purpose.

| Area | Contents |
| --- | --- |
| [architecture/](architecture/) | System overview, bounded evidence-agent design (implemented vs proposed), escalation boundaries, and component contracts. |
| [design/](design/) | [Policy interpretation](design/policy-interpretation.md) (how the supplied policy and cases are reconciled), [extraction scope](design/extraction-scope.md) and document benchmarks, and frontend design. |
| [guides/](guides/) | [Local runbook](guides/local-runbook.md), sample-document pointer, and demo outline. |
| [reports/](reports/) | Generated evidence. `make verify` owns the verification summary, supplied-case evaluation, offline suites, dirty-corpus live status, and policy audit; the website-driven live runner owns the [four-scenario outcome report](reports/live-ocr-outcome-benchmark.md). Do not edit generated reports by hand. |
| [reference/](reference/) | The evaluator's assignment and sample-documents guide (unmodified, read-only), and the [supplied artifact map](reference/submission-artifact-map.md). |
| [research/](research/) | Pre-implementation architecture research from the initial commit. It is kept as written and does not describe current behavior. |
| [screenshots/](screenshots/) | Product screenshots refreshed by the authenticated browser regression on 2026-09-27. |

The retained provider-backed synthetic run is under [`../output/live-ocr-benchmark/`](../output/live-ocr-benchmark/): raw API responses, filled-form/result screenshots, the authenticated review queue, and machine-readable results. These artifacts contain synthetic data only and do not replace the broader labelled dirty-corpus accuracy benchmark.

Start with the repository [README](../README.md) for setup and results, and the [architecture overview](architecture/overview.md) for system behavior.
