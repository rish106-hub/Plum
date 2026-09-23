"""Exercise the real submission and correction screens with synthetic PDFs."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"
SAMPLES = ROOT / "sample_documents"
SCREENSHOTS = ROOT / "docs" / "screenshots"


def _fill(page: Page, documents: list[Path]) -> dict:
    page.goto(BASE_URL, wait_until="networkidle")
    page.locator('select[name="member_id"]').select_option("EMP001")
    page.locator('select[name="claim_category"]').select_option("CONSULTATION")
    page.locator('input[name="treatment_date"]').fill("2024-11-01")
    page.locator('input[name="claimed_amount"]').fill("1500")
    page.locator('input[name="ytd_claims_amount"]').fill("0")
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
        page.screenshot(path=str(SCREENSHOTS / "approval.png"), full_page=True)

        correction = _fill(page, [rx, rx])
        assert correction["result"]["decision"] is None
        assert correction["state"] == "DOCUMENT_CORRECTION_REQUIRED"
        assert any("hospital" in message.lower() for message in correction["result"]["correction_requests"])
        assert page.locator("#correction-panel").is_visible()
        page.screenshot(path=str(SCREENSHOTS / "correction.png"), full_page=True)

        assert not errors, errors
        browser.close()

    print(
        json.dumps(
            {
                "approval": {"id": approval["id"], "decision": approval["result"]["decision"], "approved_amount": approval["result"]["approved_amount"]},
                "correction": {"id": correction["id"], "decision": correction["result"]["decision"], "state": correction["state"]},
                "browser_errors": errors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
