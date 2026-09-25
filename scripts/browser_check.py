"""Exercise the real submission and correction screens with synthetic PDFs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("PLUM_BASE_URL", "http://127.0.0.1:8000")
SAMPLES = ROOT / ".data" / "samples"
SCREENSHOTS = Path(os.getenv("PLUM_SCREENSHOT_DIR", ROOT / "docs" / "screenshots"))


def _fill(page: Page, documents: list[Path]) -> dict:
    page.goto(BASE_URL, wait_until="networkidle")
    page.locator('select[name="member_id"]').select_option("EMP001")
    page.locator('select[name="claim_category"]').select_option("CONSULTATION")
    page.locator('input[name="treatment_date"]').fill("2024-11-01")
    page.locator('input[name="claimed_amount"]').fill("1500")
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
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))

        approval = _fill(page, [rx, bill])
        assert approval["result"]["decision"] == "APPROVED", approval["result"]
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
        assert any("hospital" in message.lower() for message in correction["result"]["correction_requests"])
        assert page.locator("#correction-panel").is_visible()
        page.screenshot(path=str(SCREENSHOTS / "correction.png"), full_page=True)

        duplicate = _fill(page, [rx, bill])
        assert duplicate["result"]["decision"] == "MANUAL_REVIEW", duplicate["result"]
        assert duplicate["result"]["reasons"][0]["code"] == "DUPLICATE_BILL"
        assert page.locator("#result-content").is_visible()
        assert page.locator("#escalation-panel").is_visible()
        assert "identical bill" in page.locator("#escalation-reason").inner_text().lower()
        assert page.locator("#amount-label").inner_text() == "Amount pending review"
        assert page.locator("#approved-amount").inner_text() == "Pending"
        assert page.locator("#ledger-total").is_hidden()
        page.screenshot(path=str(SCREENSHOTS / "duplicate-review.png"), full_page=True)

        assert not errors, errors
        browser.close()

    print(
        json.dumps(
            {
                "approval": {"id": approval["id"], "decision": approval["result"]["decision"], "approved_amount": approval["result"]["approved_amount"]},
                "correction": {"id": correction["id"], "decision": correction["result"]["decision"], "state": correction["state"]},
                "duplicate_bill": {"id": duplicate["id"], "decision": duplicate["result"]["decision"], "reason": duplicate["result"]["reasons"][0]["code"]},
                "browser_errors": errors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
