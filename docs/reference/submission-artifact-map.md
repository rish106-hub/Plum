# Supplied artifact map

The original supplied materials are preserved in the repository under descriptive directories so application code, tests, and reports stay separate. This table makes the mapping explicit for reviewers and automation.

| Assignment name | Repository path | Runtime use |
| --- | --- | --- |
| `assignment.md` | [`assignment.md`](assignment.md) | Reference only |
| `policy_terms.json` | [`../../data/policy_terms.json`](../../data/policy_terms.json) | Deterministic policy source of truth |
| `test_cases.json` | [`../../tests/fixtures/test_cases.json`](../../tests/fixtures/test_cases.json) | Structured policy-pipeline regression fixture |
| `sample_documents_guide.md` | [`../guides/sample-documents.md`](../guides/sample-documents.md) | Extraction scope and synthetic-document benchmark design |

The supplied structured fixture is not used as an OCR benchmark. Actual generated PDF/image input is separately exercised by `scripts.evaluate_documents` and reported in [`../reports/document-evaluation.md`](../reports/document-evaluation.md).
