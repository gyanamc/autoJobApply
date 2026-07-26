import re
from pathlib import Path
from playwright.async_api import Page, ElementHandle
from browser_manager import browser_manager
from form_filler_agent import form_filler_agent
from config import config

async def apply_external_ats(page: Page, apply_url: str, dry_run: bool = True) -> bool:
    """
    Attempts to fill an external job application form (Lever, Greenhouse, Workday, etc.)
    using the candidate's resume data.
    """
    print(f"[External ATS] Navigating to external application page: {apply_url}")
    try:
        await page.goto(apply_url)
        # Give some time for client-side forms to render
        await browser_manager.human_delay(3.0, 5.0)
    except Exception as e:
        print(f"[External ATS] Navigation failed: {e}")
        return False

    # Check if redirected to a login page (often happens with Workday if not pre-configured)
    if "login" in page.url or "signin" in page.url:
        print("[External ATS] Skip: Form requires signing in first.")
        return False

    # Common selector patterns for ATS forms
    # 1. Look for visible text inputs, selects, files, and checkboxes
    inputs = await page.query_selector_all("input:visible, textarea:visible, select:visible")
    if not inputs:
        print("[External ATS] Warning: No visible inputs found on page.")
        return False

    print(f"[External ATS] Found {len(inputs)} form inputs. Filling them...")

    # Upload resume first as it might trigger autofill on some ATS (like Lever/Greenhouse)
    await _upload_resume_external(page)
    await browser_manager.human_delay(2.0, 3.0)

    # Process each field
    for field in inputs:
        try:
            field_type = await field.get_attribute("type") or ""
            field_type = field_type.lower()
            
            # Skip hidden, submit, radio, checkbox, file inputs (we handle file separately)
            if field_type in ("hidden", "submit", "button", "file", "checkbox", "radio"):
                continue

            # Skip if already filled by ATS parser
            curr_val = await field.input_value()
            if curr_val and curr_val.strip() != "":
                continue

            tag_name = await field.evaluate("el => el.tagName.toLowerCase()")

            # Resolve associated label or descriptive text
            label_text = await _get_field_label_text(page, field)
            if not label_text:
                continue

            if tag_name == "select":
                # Handle dropdown select
                options = await field.query_selector_all("option")
                choices = []
                for opt in options:
                    text = (await opt.inner_text()).strip()
                    if text:
                        choices.append(text)
                if not choices:
                    continue

                answer = await form_filler_agent.answer_form_question(label_text, field_type="dropdown", choices=choices)
                # Find matching value
                for opt in options:
                    opt_text = await opt.inner_text()
                    if answer.lower() in opt_text.lower() or opt_text.lower() in answer.lower():
                        val = await opt.get_attribute("value")
                        await field.select_option(val)
                        break
            else:
                # Handle text/textarea inputs
                answer = await form_filler_agent.answer_form_question(label_text, field_type="text")
                await field.focus()
                await browser_manager.human_type(field, answer)

        except Exception as field_err:
            # Continue filling other fields if one fails
            print(f"[External ATS] Error filling field: {field_err}")

    # Handle checkboxes (e.g. Terms, Privacy Policy, Consent)
    # We will check checkboxes that have text like "agree", "consent", "acknowledge", "understand"
    checkboxes = await page.query_selector_all("input[type='checkbox']:visible")
    for cb in checkboxes:
        try:
            if await cb.is_checked():
                continue
            # Look at parent text to decide if we should consent
            label_el = await page.query_selector(f"label[for='{await cb.get_attribute('id')}']")
            if label_el:
                label_text = await label_el.inner_text()
                if any(w in label_text.lower() for w in ["agree", "consent", "acknowledge", "understand", "read", "accept"]):
                    await cb.check()
                    await browser_manager.human_delay(0.2, 0.5)
        except Exception:
            pass

    # Find the submit button
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Submit Application')",
        ".submit-button",
        "#submit-button"
    ]
    
    submit_btn = None
    for selector in submit_selectors:
        btn = await page.query_selector(selector)
        if btn and await btn.is_visible():
            submit_btn = btn
            break

    if not submit_btn:
        print("[External ATS] Warning: Could not locate submit button. Form may be incomplete or multipage.")
        return False

    if dry_run:
        print("[External ATS] Dry Run: Form filled! Stopping before submission.")
        return True
    else:
        print("[External ATS] Submitting application...")
        await browser_manager.click_with_delay(submit_btn)
        await browser_manager.human_delay(4.0, 6.0)
        return True

async def _upload_resume_external(page: Page):
    """Finds the resume upload element and uploads the resume PDF."""
    file_inputs = await page.query_selector_all("input[type='file']")
    pdf_path = Path(config.RESUME_PDF_PATH)
    
    if not pdf_path.exists():
        print(f"[External ATS] Warning: Resume file not found at {pdf_path.resolve()}. Skipping upload.")
        return

    for file_input in file_inputs:
        try:
            # Check associated label text to verify it's for a Resume/CV
            label_text = await _get_field_label_text(page, file_input)
            is_resume = "resume" in label_text.lower() or "cv" in label_text.lower() or not label_text
            
            if is_resume:
                print(f"[External ATS] Uploading resume to field: '{label_text}'")
                await file_input.set_input_files(str(pdf_path.resolve()))
                break
        except Exception:
            pass

async def _get_field_label_text(page: Page, element: ElementHandle) -> str:
    """Gets descriptive text/label for a form element."""
    el_id = await element.get_attribute("id")
    if el_id:
        label = await page.query_selector(f"label[for='{el_id}']")
        if label:
            return (await label.inner_text()).strip()
            
    # Try placeholder or name attribute
    placeholder = await element.get_attribute("placeholder")
    if placeholder:
        return placeholder.strip()
        
    name = await element.get_attribute("name")
    if name:
        return name.replace("_", " ").replace("-", " ").strip()
        
    # Check parent/sibling elements
    parent = await element.query_xpath("..")
    if parent:
        text = await parent[0].inner_text()
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if lines:
            return lines[0]
            
    return ""
