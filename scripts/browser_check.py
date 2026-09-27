"""Exercise the real submission and correction screens with synthetic PDFs.

The supplied policy period is 2024-04-01 to 2025-03-31, so start the server with
the explicit development clock before running this check:

    PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05 PLUM_REVIEW_TOKEN=local-review-token \
      .venv/bin/python -m uvicorn claims.web:app

The check asserts that the demo clock is visible in the UI and decision trace.
Start the server with a new ``PLUM_DATA_DIR`` for this deterministic scenario:

    PLUM_DATA_DIR="$(mktemp -d)" PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05 \
      PLUM_REVIEW_TOKEN=local-review-token \
      .venv/bin/python -m uvicorn claims.web:app

The workflow deliberately creates repeat submissions to exercise both cleared
and confirmed duplicate findings, so an existing demo database can make the first approval
scenario fail the same-day-claims fraud threshold.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("PLUM_BASE_URL", "http://127.0.0.1:8000")
SAMPLES = ROOT / ".data" / "samples"
SCREENSHOTS = Path(os.getenv("PLUM_SCREENSHOT_DIR", ROOT / "docs" / "screenshots"))
REVIEWER_ID = os.getenv("PLUM_REVIEWER_ID", "browser-check")
REVIEW_TOKEN = os.getenv("PLUM_REVIEW_TOKEN", "")


def _fill(page: Page, documents: list[Path]) -> dict:
    page.goto(BASE_URL, wait_until="networkidle")
    page.locator('select[name="member_id"]').select_option("EMP001")
    page.locator('select[name="claim_category"]').select_option("CONSULTATION")
    page.locator('input[name="treatment_date"]').fill("2024-11-01")
    page.locator('input[name="claimed_amount"]').fill("1500")
    page.locator('select[name="pre_authorization_obtained"]').select_option("")
    page.locator('input[name="files"]').set_input_files([str(path) for path in documents])
    page.locator('#submit-button').click()
    page.wait_for_url("**/claims/*")
    page.locator('#result-content').wait_for(state="visible", timeout=30000)
    claim_id = page.url.rsplit("/", 1)[-1]
    response = page.request.get(f"{BASE_URL}/api/claims/{claim_id}")
    assert response.ok
    return response.json()


def main() -> None:
    rx = SAMPLES / "synthetic_prescription.pdf"
    bill = SAMPLES / "synthetic_hospital_bill.pdf"
    assert rx.exists() and bill.exists(), "Generate samples with python -m tools.generate_samples first"
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        assert REVIEW_TOKEN, "Set PLUM_REVIEW_TOKEN to the same value used by the server"
        page = browser.new_page(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1,
            extra_http_headers={"X-Reviewer-ID": REVIEWER_ID, "X-Reviewer-Token": REVIEW_TOKEN},
        )
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.goto(BASE_URL, wait_until="networkidle")
        assert page.locator("#demo-clock-banner").is_visible(), "Start the server with PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05"
        approval = _fill(page, [rx, bill])
        assert approval["result"]["decision"] == "APPROVED", approval["result"]
        clock_step = approval["result"]["trace"][0]
        assert clock_step["stage"] == "clock" and clock_step["evidence"]["source"] == "PLUM_DEMO_CLOCK", clock_step
        assert page.locator("#demo-clock-banner").is_visible()
        assert "PLUM_DEMO_CLOCK" in page.locator("#demo-clock-message").inner_text()
        assert approval["result"]["approved_amount"] == 1350
        assert page.locator("#trace-list .trace-entry").count() >= 10
        assert page.locator("#ledger-total").is_visible()
        assert "₹" in page.locator("#ledger-payable").inner_text()
        assert "1,350" in page.locator("#ledger-payable").inner_text()
        assert any(label in page.locator("#ai-facts").inner_text() for label in ("No model call needed", "Off"))
        page.screenshot(path=str(SCREENSHOTS / "approval.png"), full_page=True)

        # UI-only fixture verifies the grounded-evidence presentation without making a paid model call.
        approval_response = page.request.get(f"{BASE_URL}/api/claims/{approval['id']}").json()
        approval_response["result"]["document_metrics"]["gemini"] = {
            "status": "CANDIDATES_VALIDATED", "calls": 1, "retries": 0, "pages": 1,
            "input_tokens": 120, "output_tokens": 40, "total_tokens": 160,
        }
        approval_response["result"]["trace"].append({
            "stage": "gemini_evidence", "status": "CANDIDATES_APPLIED", "model": "synthetic-ui-fixture",
            "candidate_evidence": [{"file_id": "UPLOAD-2", "fields": ["total_paise"], "sources": [
                {"field": "total_paise", "page": 1, "quote": "Total amount: Rs. 1500"},
            ]}],
        })
        def serve_grounded_evidence(route):
            route.fulfill(json=approval_response)
        page.route(f"{BASE_URL}/api/claims/{approval['id']}", serve_grounded_evidence)
        page.goto(f"{BASE_URL}/claims/{approval['id']}", wait_until="networkidle")
        page.locator("#result-content").wait_for(state="visible")
        assert "Total amount: Rs. 1500" in page.locator("#evidence-list").inner_text()
        assert "Page 1" in page.locator("#evidence-list").inner_text()
        assert "160" in page.locator("#ai-facts").inner_text()
        page.screenshot(path=str(SCREENSHOTS / "evidence-ledger.png"), full_page=True)
        page.unroute(f"{BASE_URL}/api/claims/{approval['id']}", serve_grounded_evidence)

        correction = _fill(page, [rx, rx])
        assert correction["result"]["decision"] is None
        assert correction["state"] == "DOCUMENT_CORRECTION_REQUIRED"
        assert any(
            "hospital" in str(request.get("message", "")).lower()
            for request in correction["result"]["correction_requests"]
        )
        assert page.locator("#correction-panel").is_visible()
        page.screenshot(path=str(SCREENSHOTS / "correction.png"), full_page=True)

        duplicate = _fill(page, [rx, bill])
        assert duplicate["result"]["decision"] == "MANUAL_REVIEW", duplicate["result"]
        assert duplicate["result"]["reasons"][0]["code"] == "DUPLICATE_BILL"
        duplicate_issue = next(issue for issue in duplicate["result"]["review_issues"] if issue["code"] == "DUPLICATE_BILL")
        assert {"FALSE_POSITIVE", "CONFIRMED_DUPLICATE"} <= set(duplicate_issue["allowed_findings"])
        assert page.locator("#result-content").is_visible()
        assert page.locator("#escalation-panel").is_visible()
        assert "identical bill" in page.locator("#escalation-reason").inner_text().lower()
        assert page.locator("#amount-label").inner_text() == "Amount pending review"
        assert page.locator("#approved-amount").inner_text() == "Pending"
        assert page.locator("#ledger-total").is_hidden()
        page.screenshot(path=str(SCREENSHOTS / "duplicate-review.png"), full_page=True)

        page.goto(f"{BASE_URL}/ops", wait_until="networkidle")
        page.locator(f'.worklist-row[data-claim-id="{duplicate["id"]}"]').click()
        page.locator("#ops-review-form").wait_for(state="visible")
        assert page.locator("#ops-review-amount").count() == 0
        page.locator("#ops-review-issue").select_option(duplicate_issue["issue_id"])
        page.locator("#ops-review-finding").select_option("FALSE_POSITIVE")
        page.locator("#ops-review-reason-code").fill("DUPLICATE_FALSE_POSITIVE")
        page.locator("#ops-review-reason-text").fill("The two submissions are separate consultations supported by the source records.")
        page.locator("#ops-review-evidence").fill("Compared the bill hash, bill number, amount, date, and prior claim trace.")
        page.locator("#ops-review-form button[type='submit']").click()
        page.locator("#ops-review-action").wait_for(state="hidden")
        cleared = page.request.get(f"{BASE_URL}/api/claims/{duplicate['id']}").json()
        assert cleared["state"] == "DECIDED", cleared
        assert cleared["result"]["decision"] in {"APPROVED", "PARTIAL"}, cleared["result"]

        confirmed_duplicate = _fill(page, [rx, bill])
        assert confirmed_duplicate["result"]["decision"] == "MANUAL_REVIEW", confirmed_duplicate["result"]
        confirmed_issue = next(issue for issue in confirmed_duplicate["result"]["review_issues"] if issue["code"] == "DUPLICATE_BILL")
        page.goto(f"{BASE_URL}/ops", wait_until="networkidle")
        page.locator(f'.worklist-row[data-claim-id="{confirmed_duplicate["id"]}"]').click()
        page.locator("#ops-review-form").wait_for(state="visible")
        page.locator("#ops-review-issue").select_option(confirmed_issue["issue_id"])
        page.locator("#ops-review-finding").select_option("CONFIRMED_DUPLICATE")
        page.locator("#ops-review-reason-code").fill("DUPLICATE_CONFIRMED")
        page.locator("#ops-review-reason-text").fill("The submitted bill duplicates an earlier paid claim.")
        page.locator("#ops-review-evidence").fill("Compared the bill hash, bill number, amount, date, and prior claim trace.")
        page.locator("#ops-review-form button[type='submit']").click()
        page.locator("#ops-review-action").wait_for(state="hidden")
        reviewed = page.request.get(f"{BASE_URL}/api/claims/{confirmed_duplicate['id']}").json()
        assert reviewed["state"] == "DECIDED", reviewed
        assert reviewed["result"]["decision"] == "REJECTED"
        assert reviewed["result"]["approved_amount"] == 0

        assert not errors, errors
        browser.close()

    print(
        json.dumps(
            {
                "approval": {"id": approval["id"], "decision": approval["result"]["decision"], "approved_amount": approval["result"]["approved_amount"], "submission_date": approval["request"]["submission_date"], "clock": approval["result"]["trace"][0]["evidence"]},
                "correction": {"id": correction["id"], "decision": correction["result"]["decision"], "state": correction["state"]},
                "duplicate_bill": {"id": duplicate["id"], "decision": duplicate["result"]["decision"], "reason": duplicate["result"]["reasons"][0]["code"]},
                "reviewer_false_positive": {"id": cleared["id"], "decision": cleared["result"]["decision"]},
                "reviewer_confirmed_duplicate": {"id": reviewed["id"], "decision": reviewed["result"]["decision"]},
                "browser_errors": errors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
