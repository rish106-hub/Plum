# Supplied artifact map

The repository keeps the supplied assignment and fixture inputs separate from application code and reports. `policy_terms.json` is the maintained runtime policy copy (it corrects dangling roster references), and the sample guide includes local processing notes; neither is represented as a byte-for-byte preserved source artifact. This table makes those boundaries explicit for reviewers and automation.

| Assignment name | Repository path | Runtime use |
| --- | --- | --- |
| `assignment.md` | [`assignment.md`](assignment.md) | Reference only |
| `policy_terms.json` | [`../../data/policy_terms.json`](../../data/policy_terms.json) | Maintained deterministic runtime policy; validate changes with fixture and policy coverage reports |
| `test_cases.json` | [`../../tests/fixtures/test_cases.json`](../../tests/fixtures/test_cases.json) | Structured policy-pipeline regression fixture |
| `sample_documents_guide.md` | [`../guides/sample-documents.md`](../guides/sample-documents.md) | Source guide with clearly marked local processing notes; extraction scope and synthetic-document benchmark design |

The supplied structured fixture is not used as an OCR benchmark. Actual generated PDF/image input is separately exercised by `scripts.evaluate_documents` and reported in [`../reports/document-evaluation.md`](../reports/document-evaluation.md).
