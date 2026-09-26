# Supplied artifact map

The evaluator supplied four artifacts. The repository keeps all four **byte-for-byte as delivered in the evaluator's initial commit** (`c8f4791`, where they sit at the repository root). They are read-only inputs. `tests/test_source_artifacts.py` enforces this by checking each file's sha256 and scoring all 12 supplied cases against the untouched files. `make verify` rechecks the hashes, compares them with the initial commit's blobs, and records the result in [`../reports/verification-summary.md`](../reports/verification-summary.md).

| Supplied name | Repository path | sha256 | Use |
| --- | --- | --- | --- |
| `policy_terms.json` | [`../../data/policy_terms.json`](../../data/policy_terms.json) | `1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce` | Runtime policy. It is loaded only through `claims.policy.load_policy` (schema validation, then normalization into the canonical config). |
| `test_cases.json` | [`../../tests/fixtures/test_cases.json`](../../tests/fixtures/test_cases.json) | `4b9b9a047ec6a6479a81f6b2767f00f931920ad7bedb3f547abb54b771034e63` | **The sole source of truth for expected outcomes.** `scripts.evaluate` and the tests read expectations from it; no expected value is copied into code. |
| `assignment.md` | [`assignment.md`](assignment.md) | `538eb43b6b6ecd983904cbda0c8f2efcca8ea1703e8e18e6c981f85c7db0e2bc` | Reference only |
| `sample_documents_guide.md` | [`sample_documents_guide.md`](sample_documents_guide.md) | `b28f17041652a5d553abf1521088e282c38d25965d15567054bbcc0d556b939d` | Reference only |

## Where interpretation lives

These files are never edited to fit the code. Interpretation lives in two places:

- **Code.** The interpretation tables and normalizer in `claims/policy.py` turn the raw policy into a canonical config. Every derivation, interpretation and conflict resolution appears in that config's `audit` trail and in every decision's first trace step. The generated list is [`../reports/policy-audit.md`](../reports/policy-audit.md).
- **Design docs.** [`../design/policy-interpretation.md`](../design/policy-interpretation.md) explains each rule and the policy/fixture contradiction it resolves. [`../design/extraction-scope.md`](../design/extraction-scope.md) sets out our reading of the sample documents guide.

The supplied structured fixture contains no document bytes, so it is not an OCR benchmark. Generated PDF and image input is tested separately by `scripts.evaluate_documents`, reported in [`../reports/document-evaluation.md`](../reports/document-evaluation.md).
