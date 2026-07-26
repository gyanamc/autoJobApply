import re
import os
from pathlib import Path
from playwright.async_api import Page, ElementHandle
from browser_manager import browser_manager
from form_filler_agent import form_filler_agent
from config import config

async def apply_linkedin_easy_apply(page: Page, job_id: str, dry_run: bool = True) -> bool:
    """
    Automates the LinkedIn Easy Apply modal flow.
    If dry_run is True, it will fill out all forms but stop before clicking 'Submit application'.
    """
    print(f"[LinkedIn Easy Apply] Starting application for Job {job_id}...")
    
    # 1. Click "Easy Apply" button
    try:
        # Wait for the apply button container to load and become visible
        await page.wait_for_selector("button.jobs-apply-button", timeout=8000)
    except Exception:
        pass
        
    easy_apply_btn = await page.query_selector("button:has-text('Easy Apply'), button.jobs-apply-button:has-text('Easy Apply')")
    if not easy_apply_btn:
        # Check if already applied
        applied_indicator = await page.query_selector("button:has-text('Applied'), .artdeco-button--disabled:has-text('Applied'), :has-text('Applied on')")
        if applied_indicator:
            print("[LinkedIn Easy Apply] Already applied to this job.")
            return True
        print("[LinkedIn Easy Apply] Easy Apply button not found. Maybe already applied or expired.")
        return False
        
    await browser_manager.click_with_delay(easy_apply_btn)
    
    # Wait for modal to appear
    try:
        await page.wait_for_selector(".jobs-easy-apply-modal", timeout=8000)
    except Exception:
        print("[LinkedIn Easy Apply] Easy Apply modal did not appear.")
        return False

    max_steps = 12
    step = 0
    
    while step < max_steps:
        step += 1
        print(f"[LinkedIn Easy Apply] Processing Step {step}...")
        await browser_manager.human_delay(1.5, 2.5)

        # Extract current modal elements
        modal = await page.query_selector(".jobs-easy-apply-modal")
        if not modal:
            print("[LinkedIn Easy Apply] Modal closed unexpectedly.")
            return False

        # Fill out any form fields on the current page
        await _fill_form_fields(page, modal)

        # Check for navigation buttons
        submit_btn = await modal.query_selector("button:has-text('Submit application')")
        review_btn = await modal.query_selector("button:has-text('Review')")
        next_btn = await modal.query_selector("button:has-text('Next')")
        
        # Check for error messages (like required fields not filled correctly)
        error_elements = await modal.query_selector_all(".artdeco-inline-feedback--error")
        if error_elements and len(error_elements) > 0:
            print(f"[LinkedIn Easy Apply] Warning: Found {len(error_elements)} validation errors on step {step}. Attempting to resolve...")

        if submit_btn:
            if dry_run:
                print("[LinkedIn Easy Apply] Dry Run: Form filled successfully! Stopping before Submit.")
                # Close modal
                close_btn = await modal.query_selector("button[aria-label='Dismiss']")
                if close_btn:
                    await close_btn.click()
                    # Confirm discard if prompted
                    discard_btn = await page.query_selector("button:has-text('Discard')")
                    if discard_btn:
                        await discard_btn.click()
                return True
            else:
                print("[LinkedIn Easy Apply] Clicking Submit application!")
                await browser_manager.click_with_delay(submit_btn)
                await browser_manager.human_delay(3.0, 5.0)
                
                # Check for post-apply screen or confirmation
                # Click Done if it exists
                done_btn = await page.query_selector("button:has-text('Done')")
                if done_btn:
                    await done_btn.click()
                return True
                
        elif review_btn:
            print("[LinkedIn Easy Apply] Clicking Review...")
            await browser_manager.click_with_delay(review_btn)
            
        elif next_btn:
            print("[LinkedIn Easy Apply] Clicking Next...")
            await browser_manager.click_with_delay(next_btn)
            
        else:
            # No next/submit button, check if already submitted or stuck
            print("[LinkedIn Easy Apply] Stuck: No Next, Review, or Submit button found.")
            # Discard and return False
            close_btn = await modal.query_selector("button[aria-label='Dismiss']")
            if close_btn:
                await close_btn.click()
                discard_btn = await page.query_selector("button:has-text('Discard')")
                if discard_btn:
                    await discard_btn.click()
            return False

    print("[LinkedIn Easy Apply] Reached maximum wizard steps limit. Application incomplete.")
    return False

async def _fill_form_fields(page: Page, modal: ElementHandle):
    """Identifies and fills form fields in the modal."""
    
    # 1. Handle Text and TextArea Fields
    text_fields = await modal.query_selector_all("input[type='text'], textarea")
    for field in text_fields:
        # Check if already filled
        val = await field.input_value()
        if val.strip():
            continue
            
        # Find associated label text
        label_text = await _get_associated_label_text(page, field)
        if not label_text:
            continue
            
        print(f"[LinkedIn Easy Apply] Answering text question: '{label_text}'")
        answer = await form_filler_agent.answer_form_question(label_text, field_type="text")
        await field.focus()
        await browser_manager.human_type(field, answer)

    # 2. Handle Dropdown/Select Fields
    selects = await modal.query_selector_all("select")
    for select in selects:
        label_text = await _get_associated_label_text(page, select)
        if not label_text:
            continue
            
        # Get options
        options = await select.query_selector_all("option")
        choices = []
        for opt in options:
            val = await opt.get_attribute("value")
            text = await opt.inner_text()
            if val and val != "" and text.strip():
                choices.append(text.strip())
                
        if not choices:
            continue
            
        # Check if already selected (not the default placeholder)
        curr_val = await select.input_value()
        if curr_val and curr_val != "Select an option" and curr_val != "":
            # Already selected
            continue
            
        print(f"[LinkedIn Easy Apply] Answering dropdown question: '{label_text}' with choices {choices}")
        answer = await form_filler_agent.answer_form_question(label_text, field_type="dropdown", choices=choices)
        
        # Select the option that matches
        for opt in options:
            opt_text = await opt.inner_text()
            if answer.lower() in opt_text.lower() or opt_text.lower() in answer.lower():
                val = await opt.get_attribute("value")
                await select.select_option(val)
                await browser_manager.human_delay(0.5, 1.0)
                break

    # 3. Handle Radio Button Questions
    # Radio buttons are usually grouped under fieldsets with a <legend> as the question
    fieldsets = await modal.query_selector_all("fieldset")
    for fieldset in fieldsets:
        legend_el = await fieldset.query_selector("legend")
        if not legend_el:
            continue
        question_text = (await legend_el.inner_text()).strip()
        
        # Check if any radio button in this fieldset is already checked
        radios = await fieldset.query_selector_all("input[type='radio']")
        already_checked = False
        choices = []
        for radio in radios:
            if await radio.is_checked():
                already_checked = True
            # Find label for this radio
            radio_id = await radio.get_attribute("id")
            if radio_id:
                radio_label = await fieldset.query_selector(f"label[for='{radio_id}']")
                if radio_label:
                    choices.append((await radio_label.inner_text()).strip())
                    
        if already_checked or not choices:
            continue
            
        print(f"[LinkedIn Easy Apply] Answering radio question: '{question_text}' with choices {choices}")
        answer = await form_filler_agent.answer_form_question(question_text, field_type="radio", choices=choices)
        
        # Select the correct radio option
        for radio in radios:
            radio_id = await radio.get_attribute("id")
            if radio_id:
                radio_label = await fieldset.query_selector(f"label[for='{radio_id}']")
                if radio_label:
                    lbl_text = (await radio_label.inner_text()).strip()
                    if answer.lower() in lbl_text.lower() or lbl_text.lower() in answer.lower():
                        # Click the label to check the radio
                        await browser_manager.click_with_delay(radio_label)
                        break

    # 4. Handle Resume/File Upload
    file_inputs = await modal.query_selector_all("input[type='file']")
    for file_input in file_inputs:
        label_text = await _get_associated_label_text(page, file_input)
        # Check if file upload is for a resume
        is_resume = "resume" in label_text.lower() or "cv" in label_text.lower() or not label_text
        
        if is_resume:
            pdf_path = Path(config.RESUME_PDF_PATH)
            if pdf_path.exists():
                print(f"[LinkedIn Easy Apply] Uploading resume: {pdf_path.name}")
                await file_input.set_input_files(str(pdf_path.resolve()))
                await browser_manager.human_delay(2.0, 4.0)
            else:
                print(f"[LinkedIn Easy Apply] Warning: Resume file not found at {pdf_path.resolve()}. Skipping upload.")

async def _get_associated_label_text(page: Page, element: ElementHandle) -> str:
    """Helper to find the text label associated with a form element."""
    el_id = await element.get_attribute("id")
    if el_id:
        # 1. Try finding label with 'for' attribute matching the id
        label = await page.query_selector(f"label[for='{el_id}']")
        if label:
            return (await label.inner_text()).strip()
            
    # 2. Try finding parent or ancestor label
    parent = await element.query_xpath("..")
    if parent and len(parent) > 0:
        parent_el = parent[0]
        if await parent_el.evaluate("el => el.tagName.toLowerCase() === 'label'"):
            return (await parent_el.inner_text()).strip()
            
    # 3. Try finding closest label preceding the element
    # Look at the text content of the parent wrapper
    wrapper = await element.query_xpath("../..")
    if wrapper and len(wrapper) > 0:
        text = await wrapper[0].inner_text()
        # Truncate text before the element if possible, or clean it up
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if lines:
            return lines[0]
            
    return ""
