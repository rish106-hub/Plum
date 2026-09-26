"""One-command submission verification.

Runs every check a reviewer would otherwise run by hand, regenerates every
report under ``docs/reports``, and writes ``docs/reports/verification-summary``
(``.json`` and ``.md``). Numbers quoted in the README come from that summary:
the block between the ``verification:start``/``verification:end`` markers in
``README.md`` is rewritten from it, and a drift check fails the run if any
other hand-written test count or score in the docs disagrees with it.

Steps:

1. sha256 of the four evaluator-supplied artifacts (read-only inputs).
2. ruff, mypy, compileall, pytest.
3. ``scripts.evaluate`` (12 supplied cases) and offline ``scripts.evaluate_documents``.
4. Live OCR status. Verification is offline by default: provider keys are
   stripped from every child process, and live OCR is recorded as ``NOT_RUN``
   with its reason. ``--live`` runs ``scripts.evaluate_documents --providers live``
   with the caller's environment (this calls paid providers).
5. ``docs/reports/policy-audit.md`` from the canonical policy's audit trail.

Usage: ``python -m scripts.verify_submission [--live]`` (or ``make verify``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs" / "reports"
README = ROOT / "README.md"
PYTHON = sys.executable

# Evaluator-supplied artifacts, sha256 as delivered in the evaluator's initial
# commit (where they sit at the repository root). Mirrors tests/test_source_artifacts.py.
INITIAL_COMMIT = "c8f4791"
SOURCE_ARTIFACTS = {
    "data/policy_terms.json": ("policy_terms.json", "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce"),
    "tests/fixtures/test_cases.json": ("test_cases.json", "4b9b9a047ec6a6479a81f6b2767f00f931920ad7bedb3f547abb54b771034e63"),
    "docs/reference/assignment.md": ("assignment.md", "538eb43b6b6ecd983904cbda0c8f2efcca8ea1703e8e18e6c981f85c7db0e2bc"),
    "docs/reference/sample_documents_guide.md": ("sample_documents_guide.md", "b28f17041652a5d553abf1521088e282c38d25965d15567054bbcc0d556b939d"),
}
PROVIDER_ENV = ("SARVAM_API_KEY", "GEMINI_API_KEY", "GEMINI_EVIDENCE_REVIEW_ENABLED")
LIVE_COMMAND = "PYTHONPATH=. .venv/bin/python -m scripts.evaluate_documents --providers live [--gemini]"
README_START = "<!-- verification:start -->"
README_END = "<!-- verification:end -->"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _offline_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in PROVIDER_ENV}
    env["PYTHONPATH"] = str(ROOT)
    # A developer's demo clock must not leak into tests or evaluations.
    env.pop("PLUM_DEMO_CLOCK", None)
    env.pop("PLUM_ENV", None)
    return env


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _git_blob_sha256(commit: str, path: str) -> str | None:
    try:
        completed = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, check=False)
    except OSError:
        return None
    return hashlib.sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


class Runner:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def run(self, name: str, args: list[str], env: dict[str, str] | None = None, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
        print(f"==> {name}: {' '.join(args)}", flush=True)
        started = time.perf_counter()
        completed = subprocess.run(args, cwd=ROOT, env=env or _offline_env(), capture_output=True, text=True, check=False)
        seconds = round(time.perf_counter() - started, 2)
        ok = completed.returncode in ok_codes
        output = (completed.stdout + completed.stderr).strip()
        tail = "\n".join(output.splitlines()[-4:])
        print(("    ok" if ok else f"    FAILED (exit {completed.returncode})") + f" in {seconds}s", flush=True)
        if tail:
            print("    " + tail.replace("\n", "\n    "), flush=True)
        step: dict[str, Any] = {"name": name, "command": " ".join(Path(a).name if a == PYTHON else a for a in args), "exit_code": completed.returncode, "ok": ok, "seconds": seconds}
        if not ok:
            step["output_tail"] = "\n".join(output.splitlines()[-30:])
        self.steps.append(step)
        return completed


def check_artifacts() -> list[dict[str, Any]]:
    records = []
    for path, (initial_path, expected) in SOURCE_ARTIFACTS.items():
        actual = _sha256(ROOT / path)
        initial = _git_blob_sha256(INITIAL_COMMIT, initial_path)
        records.append({
            "path": path,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "ok": actual == expected,
            "matches_initial_commit": None if initial is None else initial == actual,
        })
    return records


def parse_pytest(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "subtests_passed": 0, "subtests_failed": 0, "warnings": 0}
    summary = next((line for line in reversed(output.splitlines()) if re.search(r"\d+ (passed|failed|error)", line)), "")
    for number, label in re.findall(r"(\d+) (subtests passed|subtests failed|passed|failed|errors?|skipped|warnings?)", summary):
        key = {"subtests passed": "subtests_passed", "subtests failed": "subtests_failed", "error": "errors", "warning": "warnings"}.get(label, label)
        counts[key] = int(number)
    return counts


def fixture_summary() -> dict[str, Any]:
    data = json.loads((REPORT_DIR / "evaluation-data.json").read_text(encoding="utf-8"))
    records = data["records"]
    return {
        "report": "docs/reports/evaluation.md",
        "fixture": "tests/fixtures/test_cases.json (unmodified)",
        "passed": sum(bool(record["matched"]) for record in records),
        "total": len(records),
        "fingerprints": data["fingerprints"],
        "cases": [
            {
                "case_id": record["case_id"],
                "expected_decision": record["expected"].get("decision"),
                "decision": record["output"]["decision"],
                "expected_amount": record["expected"].get("approved_amount"),
                "approved_amount": record["output"]["approved_amount"],
                "confidence": record["output"]["confidence_score"],
                "matched": record["matched"],
            }
            for record in records
        ],
    }


def document_summary() -> dict[str, Any]:
    data = json.loads((REPORT_DIR / "document-evaluation.json").read_text(encoding="utf-8"))
    return {
        "report": "docs/reports/document-evaluation.md",
        "ocr_evaluated": data["ocr_evaluated"],
        "passed": data["passed"],
        "total": data["total"],
        "suites": {name: {"passed": suite["passed"], "total": suite["total"]} for name, suite in data["suites"].items()},
    }


def live_ocr(runner: Runner, live: bool) -> dict[str, Any]:
    report = REPORT_DIR / "document-ocr-evaluation.json"
    if live:
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        runner.run("live OCR benchmark", [PYTHON, "-m", "scripts.evaluate_documents", "--providers", "live"], env=env, ok_codes=(0, 2))
        data = json.loads(report.read_text(encoding="utf-8"))
        return {"status": data["status"], "reason": data.get("reason"), "metrics": data.get("metrics"), "report": "docs/reports/document-ocr-evaluation.md", "command": LIVE_COMMAND}
    previous = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    if previous and previous.get("status") == "RUN":
        # Never overwrite a real provider run with an offline placeholder.
        return {
            "status": "NOT_RUN",
            "reason": "verification is offline by design; the committed live report comes from an earlier provider run and was not re-measured",
            "report": "docs/reports/document-ocr-evaluation.md",
            "command": LIVE_COMMAND,
        }
    # Record the NOT_RUN report with provider keys stripped, so no provider is called.
    runner.run("live OCR status (keys stripped)", [PYTHON, "-m", "scripts.evaluate_documents", "--providers", "live"], ok_codes=(2,))
    reason = "verification is offline by design (provider keys are stripped); "
    reason += "SARVAM_API_KEY is set in this environment - run the live command to measure" if os.getenv("SARVAM_API_KEY") else "SARVAM_API_KEY is not configured"
    return {"status": "NOT_RUN", "reason": reason, "metrics": None, "report": "docs/reports/document-ocr-evaluation.md", "command": LIVE_COMMAND}


def write_policy_audit() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from claims.policy import load_policy

    policy = load_policy(ROOT / "data" / "policy_terms.json")
    audit = policy["audit"]
    kinds: dict[str, int] = {}
    for entry in audit:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    lines = [
        "# Policy interpretation audit (generated)",
        "",
        "Generated by `scripts/verify_submission.py` from the canonical config that `claims.policy.load_policy` builds from the unmodified",
        "`data/policy_terms.json`. Every entry is an engine judgment or derivation, not policy text. The reasoning behind the",
        "conflict resolutions is in [`../design/policy-interpretation.md`](../design/policy-interpretation.md).",
        "",
        f"- Policy: `{policy['policy_id']}`; schema `{policy['schema_version']}`",
        f"- Source sha256: `{policy['source']['sha256']}`",
        f"- Canonical sha256: `{policy['canonical_sha256']}`",
        f"- Audit entries: {len(audit)} (" + ", ".join(f"{kind} {count}" for kind, count in sorted(kinds.items())) + ")",
        "",
        "| Id | Kind | Policy paths | Description |",
        "| --- | --- | --- | --- |",
    ]
    for entry in audit:
        paths = "<br>".join(f"`{path}`" for path in entry["source_paths"])
        description = str(entry["description"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{entry['id']}` | {entry['kind']} | {paths} | {description} |")
    (REPORT_DIR / "policy-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": "docs/reports/policy-audit.md", "entries": len(audit), "by_kind": kinds, "canonical_sha256": policy["canonical_sha256"]}


def _score(section: dict[str, Any]) -> str:
    return f"{section['passed']}/{section['total']}"


def readme_block(summary: dict[str, Any]) -> str:
    tests = summary["tests"]
    fixture = summary["fixture_evaluation"]
    documents = summary["document_evaluation"]
    live = summary["live_ocr"]
    suites = ", ".join(f"`{name}` {_score(suite)}" for name, suite in documents["suites"].items())
    git = summary["git"]
    head = (git["head"] or "unknown")[:7] + (" + uncommitted changes" if git["worktree_dirty"] else "")
    rows = [
        README_START,
        f"_Generated by `make verify` at {summary['finished_at']} on `{head}`. Source: [`docs/reports/verification-summary.md`](docs/reports/verification-summary.md)._",
        "",
        "| Check | Result |",
        "| --- | --- |",
        f"| Supplied artifacts (sha256) | {sum(a['ok'] for a in summary['source_artifacts'])}/{len(summary['source_artifacts'])} unmodified |",
        f"| pytest | {tests['passed']} passed, {tests['failed']} failed, {tests['subtests_passed']} subtests passed |",
        f"| ruff / mypy / compileall | {'pass' if summary['lint_ok'] else 'FAIL'} / {'pass' if summary['typecheck_ok'] else 'FAIL'} / {'pass' if summary['compile_ok'] else 'FAIL'} |",
        f"| Supplied cases (`scripts.evaluate`, unmodified `test_cases.json`) | **{_score(fixture)}** |",
        f"| Offline document suites (routing / fail-closed, not OCR) | **{_score(documents)}**: {suites} |",
        f"| Live OCR accuracy | **{live['status']}**: {live['reason']} |",
        README_END,
    ]
    return "\n".join(rows)


def update_readme(summary: dict[str, Any]) -> bool:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if not pattern.search(text):
        return False
    README.write_text(pattern.sub(lambda _: readme_block(summary), text, count=1), encoding="utf-8")
    return True


def docs_drift(summary: dict[str, Any]) -> list[str]:
    """Hand-written counts and scores in the docs must come from this summary."""
    allowed = {_score(summary["fixture_evaluation"]), _score(summary["document_evaluation"])}
    allowed |= {_score(suite) for suite in summary["document_evaluation"]["suites"].values()}
    allowed.add(f"{len(summary['source_artifacts'])}/{len(summary['source_artifacts'])}")
    skip = {ROOT / "docs" / "reference" / "assignment.md", ROOT / "docs" / "reference" / "sample_documents_guide.md"}
    files = [README, *sorted((ROOT / "docs").rglob("*.md"))]
    issues = []
    for path in files:
        if path in skip or path.parent == REPORT_DIR:
            continue  # evaluator inputs and generated reports
        text = path.read_text(encoding="utf-8")
        text = re.sub(re.escape(README_START) + r".*?" + re.escape(README_END), "", text, flags=re.S)
        text = re.sub(r"https?://\S+", "", text)
        relative = path.relative_to(ROOT)
        for match in re.finditer(r"\b(\d+) (?:tests? )?passed\b", text):
            issues.append(f"{relative}: hand-written test count '{match.group(0)}' (quote the verification summary instead)")
        for match in re.finditer(r"(?<![\w/.-])(\d+)/(\d+)(?![\w/.-])", text):
            if match.group(0) not in allowed:
                issues.append(f"{relative}: score '{match.group(0)}' is not in the verification summary")
    return issues


def markdown(summary: dict[str, Any]) -> str:
    fixture = summary["fixture_evaluation"]
    documents = summary["document_evaluation"]
    live = summary["live_ocr"]
    tests = summary["tests"]
    git = summary["git"]
    lines = [
        "# Verification summary (generated)",
        "",
        f"Result: **{'PASS' if summary['ok'] else 'FAIL'}**. Generated by `make verify` (`scripts/verify_submission.py`); do not edit by hand.",
        "",
        f"- Git HEAD: `{git['head']}` on `{git['branch']}`" + (f" (worktree had uncommitted changes in {git['changed_paths']} path(s) when this ran)" if git["worktree_dirty"] else " (clean worktree)"),
        f"- Started {summary['started_at']}, finished {summary['finished_at']}",
        f"- Python {summary['python']} on {summary['platform']}",
        "",
        "## Supplied artifacts",
        "",
        f"Checked against the sha256 of the evaluator's initial commit `{INITIAL_COMMIT}`. These files are read-only inputs; `tests/fixtures/test_cases.json` is the sole source of truth for expected outcomes.",
        "",
        "| Path | sha256 | Unmodified | Same bytes as initial commit |",
        "| --- | --- | --- | --- |",
    ]
    for artifact in summary["source_artifacts"]:
        initial = artifact["matches_initial_commit"]
        lines.append(f"| `{artifact['path']}` | `{artifact['actual_sha256']}` | {'yes' if artifact['ok'] else 'NO'} | {'n/a (commit unavailable)' if initial is None else ('yes' if initial else 'NO')} |")
    lines += [
        "",
        "## Steps",
        "",
        "| Step | Command | Exit | Seconds |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {step['name']} | `{step['command']}` | {step['exit_code']}{'' if step['ok'] else ' (FAILED)'} | {step['seconds']} |" for step in summary["steps"]]
    lines += [
        "",
        "## Tests",
        "",
        f"pytest: {tests['passed']} passed, {tests['failed']} failed, {tests['errors']} errors, {tests['skipped']} skipped, {tests['subtests_passed']} subtests passed, {tests['warnings']} warnings.",
        "",
        "## Supplied cases",
        "",
        f"**{_score(fixture)}** matched on decision, amount, rejection codes, confidence and the explicit behavior checks in `scripts/evaluate.py`, against the unmodified fixture. Full traces: [evaluation.md](evaluation.md).",
        "",
        f"- Policy file sha256 `{fixture['fingerprints']['policy_sha256']}`",
        f"- Canonical policy sha256 `{fixture['fingerprints']['policy_canonical_sha256']}`",
        f"- Fixture sha256 `{fixture['fingerprints']['fixture_sha256']}`",
        "",
        "| Case | Expected | Produced | Expected amount | Amount | Confidence | Match |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for case in fixture["cases"]:
        expected_amount = "-" if case["expected_amount"] is None else case["expected_amount"]
        lines.append(f"| {case['case_id']} | {case['expected_decision']} | {case['decision']} | {expected_amount} | {case['approved_amount']} | {case['confidence']} | {'yes' if case['matched'] else 'NO'} |")
    lines += [
        "",
        "## Document suites (offline)",
        "",
        f"**{_score(documents)}** scenarios matched. These suites test intake routing and fail-closed behavior with no provider calls; they do not measure OCR accuracy. Report: [document-evaluation.md](document-evaluation.md).",
        "",
        "| Suite | Result |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | {_score(suite)} |" for name, suite in documents["suites"].items()]
    lines += [
        "",
        "## Live OCR",
        "",
        f"Status: **{live['status']}**. {live['reason']}.",
        "",
        "Live OCR accuracy is unmeasured until this runs with provider keys:",
        "",
        "```bash",
        live["command"],
        "```",
        "",
        "## Policy interpretation audit",
        "",
        f"{summary['policy_audit']['entries']} audit entries in the canonical config; see [policy-audit.md](policy-audit.md).",
        "",
        "## Documentation drift",
        "",
    ]
    lines += [f"- {issue}" for issue in summary["docs_drift"]] or ["No hand-written count or score in README.md or docs/ disagrees with this summary."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--live", action="store_true", help="also run the live OCR benchmark with the caller's provider keys (paid calls)")
    args = parser.parse_args()
    started = _now()
    runner = Runner()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("==> supplied artifact hashes", flush=True)
    artifacts = check_artifacts()
    for artifact in artifacts:
        print(f"    {'ok' if artifact['ok'] else 'MODIFIED'} {artifact['path']}", flush=True)

    lint = runner.run("ruff", [PYTHON, "-m", "ruff", "check", "."])
    typecheck = runner.run("mypy", [PYTHON, "-m", "mypy", "claims", "scripts", "tools"])
    compiled = runner.run("compileall", [PYTHON, "-m", "compileall", "-q", "claims", "scripts", "tools"])
    tests = runner.run("pytest", [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    runner.run("supplied-case evaluation", [PYTHON, "-m", "scripts.evaluate"])
    runner.run("offline document suites", [PYTHON, "-m", "scripts.evaluate_documents"])
    live = live_ocr(runner, args.live)
    audit = write_policy_audit()

    head = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    summary: dict[str, Any] = {
        "schema": "plum.verification_summary.v1",
        "started_at": started,
        "finished_at": _now(),
        "git": {
            "head": head,
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "worktree_dirty": bool(status),
            "changed_paths": len(status.splitlines()) if status else 0,
        },
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "source_artifacts": artifacts,
        "steps": runner.steps,
        "lint_ok": lint.returncode == 0,
        "typecheck_ok": typecheck.returncode == 0,
        "compile_ok": compiled.returncode == 0,
        "tests": parse_pytest(tests.stdout + tests.stderr),
        "fixture_evaluation": fixture_summary(),
        "document_evaluation": document_summary(),
        "live_ocr": live,
        "policy_audit": audit,
    }
    summary["readme_block_updated"] = update_readme(summary)
    summary["docs_drift"] = docs_drift(summary)
    summary["ok"] = (
        all(artifact["ok"] for artifact in artifacts)
        and all(step["ok"] for step in runner.steps)
        and summary["fixture_evaluation"]["passed"] == summary["fixture_evaluation"]["total"]
        and summary["document_evaluation"]["passed"] == summary["document_evaluation"]["total"]
        and summary["readme_block_updated"]
        and not summary["docs_drift"]
    )
    (REPORT_DIR / "verification-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (REPORT_DIR / "verification-summary.md").write_text(markdown(summary), encoding="utf-8")

    print("", flush=True)
    print(f"supplied artifacts unmodified: {sum(a['ok'] for a in artifacts)}/{len(artifacts)}")
    print(f"pytest: {summary['tests']['passed']} passed, {summary['tests']['failed']} failed, {summary['tests']['subtests_passed']} subtests passed")
    print(f"supplied cases: {_score(summary['fixture_evaluation'])}")
    print(f"offline document suites: {_score(summary['document_evaluation'])}")
    print(f"live OCR: {live['status']} ({live['reason']})")
    for issue in summary["docs_drift"]:
        print(f"docs drift: {issue}")
    if not summary["readme_block_updated"]:
        print("README.md is missing the verification markers")
    print(f"VERIFY {'PASSED' if summary['ok'] else 'FAILED'}; wrote docs/reports/verification-summary.md")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
