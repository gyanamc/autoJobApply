import asyncio
from playwright.async_api import Page, ElementHandle
from browser_manager import browser_manager
from external_form_filler import apply_external_ats
from form_filler_agent import form_filler_agent

async def apply_naukri_easy_apply(page: Page, job_id: str, dry_run: bool = True) -> bool:
    """
    Automates Naukri's internal apply. Clicking the 'Apply' button on the detail page 
    submits the user's pre-filled Naukri profile. Also handles post-apply questionnaires.
    """
    print(f"[Naukri Easy Apply] Navigating to job page for Job {job_id}...")
    
    # Selectors for apply button
    apply_btn_selectors = [
        "#apply-button",
        ".apply-button",
        "button:has-text('Apply')",
        "button.apply-button",
        "input[value='Apply']"
    ]
    
    apply_btn = None
    for selector in apply_btn_selectors:
        btn = await page.query_selector(selector)
        if btn and await btn.is_visible():
            apply_btn = btn
            break
            
    if not apply_btn:
        already_applied = await page.query_selector("button:has-text('Applied'), .applied")
        if already_applied:
            print("[Naukri Easy Apply] Already applied to this job.")
            return True
        print("[Naukri Easy Apply] Apply button not found on Naukri job page.")
        return False
        
    btn_text = (await apply_btn.inner_text()).strip()
    print(f"[Naukri Easy Apply] Found apply button with text: '{btn_text}'")

    if dry_run:
        print("[Naukri Easy Apply] Dry Run: Apply button found successfully! Checking if clicked immediately or if there's a pre-apply form...")
        # In a real run we would click the button. For dry run, let's click it to see if a modal opens,
        # but we MUST NOT submit the final form. 
        # Clicking the initial "Apply" button is safe because if it's a simple apply it applies immediately,
        # but if it requires questions it will open a form.
        # Wait, if we click the button in dry-run, we might actually submit if there are NO questions!
        # So to be absolutely safe in dry-run: we do not click the button if it's a direct apply.
        # But wait! If the user wants the agent to run automatically, we should treat clicking "Apply" as the submission itself for simple jobs.
        # So for a pure dry-run, we stop here.
        print("[Naukri Easy Apply] Dry Run: Stopping before clicking Apply.")
        return True
    else:
        print("[Naukri Easy Apply] Clicking Apply to submit Naukri profile...")
        await browser_manager.click_with_delay(apply_btn)
        await browser_manager.human_delay(3.0, 5.0)
        
        # Check if a custom question/questionnaire appeared (modal, chatbot, or form)
        has_questions = await _detect_naukri_questionnaire(page)
        if has_questions:
            print("[Naukri Easy Apply] Detected post-apply questionnaire. Filling answers...")
            success = await _fill_naukri_questionnaire(page, dry_run=False)
            return success

        # Confirm simple apply success
        applied_check = await page.query_selector("button:has-text('Applied'), .applied, :has-text('applied successfully')")
        if applied_check:
            print("[Naukri Easy Apply] Confirmed: Profile submitted successfully!")
            return True
        
        return True

async def _detect_naukri_questionnaire(page: Page) -> bool:
    """Checks if a questionnaire modal, form, or chatbot is active on the page."""
    # Look for visible input fields that are NOT part of standard page headers/search bars
    visible_inputs = await page.query_selector_all("input:visible, textarea:visible, select:visible")
    
    # Filter out header search/nav inputs (like search query box, location box, etc.)
    form_inputs = []
    for el in visible_inputs:
        el_id = await el.get_attribute("id") or ""
        el_class = await el.get_attribute("class") or ""
        el_name = await el.get_attribute("name") or ""
        
        # Skip search inputs
        if any(keyword in (el_id + el_class + el_name).lower() for keyword in ["search", "suggest", "navbar", "header"]):
            continue
        form_inputs.append(el)
        
    return len(form_inputs) > 0

async def _fill_naukri_questionnaire(page: Page, dry_run: bool = False) -> bool:
    """Fills Naukri's questionnaire forms automatically using the candidate's resume."""
    max_steps = 5
    step = 0
    
    while step < max_steps:
        step += 1
        await browser_manager.human_delay(1.0, 2.0)
        
        # Scan for active form elements
        visible_inputs = await page.query_selector_all("input:visible, textarea:visible, select:visible")
        form_inputs = []
        for el in visible_inputs:
            el_id = await el.get_attribute("id") or ""
            el_class = await el.get_attribute("class") or ""
            el_name = await el.get_attribute("name") or ""
            if any(keyword in (el_id + el_class + el_name).lower() for keyword in ["search", "suggest", "navbar", "header"]):
                continue
            form_inputs.append(el)

        if not form_inputs:
            print("[Naukri Easy Apply] No active questionnaire fields found.")
            break

        print(f"[Naukri Easy Apply] Step {step}: Found {len(form_inputs)} questionnaire fields to fill.")
        
        for field in form_inputs:
            try:
                field_type = await field.get_attribute("type") or ""
                field_type = field_type.lower()
                
                if field_type in ("hidden", "submit", "button", "file"):
                    continue

                if field_type in ("radio", "checkbox"):
                    try:
                        if field_type == "checkbox" and not await field.is_checked():
                            try:
                                await field.check(timeout=3000)
                            except Exception:
                                await field.evaluate("el => { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }")
                        elif field_type == "radio":
                            try:
                                await field.click(timeout=3000)
                            except Exception:
                                await field.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', {bubbles: true})); }")
                    except Exception as click_err:
                        print(f"[Naukri Easy Apply] Error clicking radio/checkbox: {click_err}")
                    continue

                # Get label text
                label_text = await _get_naukri_field_label(page, field)
                if not label_text:
                    continue

                tag_name = await field.evaluate("el => el.tagName.toLowerCase()")
                
                if tag_name == "select":
                    options = await field.query_selector_all("option")
                    choices = [((await opt.inner_text()).strip()) for opt in options if (await opt.inner_text()).strip()]
                    if not choices:
                        continue
                        
                    answer = await form_filler_agent.answer_form_question(label_text, field_type="dropdown", choices=choices)
                    for opt in options:
                        opt_text = await opt.inner_text()
                        if answer.lower() in opt_text.lower() or opt_text.lower() in answer.lower():
                            await field.select_option(await opt.get_attribute("value"))
                            break
                else:
                    # Text/Number field
                    answer = await form_filler_agent.answer_form_question(label_text, field_type="text")
                    # Clear field if pre-filled with wrong placeholder
                    await field.fill("")
                    await field.focus()
                    await browser_manager.human_type(field, answer)

            except Exception as e:
                print(f"[Naukri Easy Apply] Error filling field: {e}")

        # Find submit/continue button for the questionnaire
        submit_btn_selectors = [
            "button:has-text('Submit')",
            "button:has-text('Save & Apply')",
            "button:has-text('Continue')",
            "button:has-text('Next')",
            ".submit-button",
            ".save-button"
        ]
        
        submit_btn = None
        for selector in submit_btn_selectors:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                submit_btn = btn
                break
                
        if not submit_btn:
            print("[Naukri Easy Apply] No submit/continue button found in questionnaire.")
            return False

        if dry_run:
            print("[Naukri Easy Apply] Dry Run: Questionnaire filled! Stopping before clicking submit.")
            return True
        else:
            print("[Naukri Easy Apply] Clicking Continue/Submit...")
            await browser_manager.click_with_delay(submit_btn)
            await browser_manager.human_delay(2.0, 4.0)

    return True

async def _get_naukri_field_label(page: Page, element: ElementHandle) -> str:
    """Helper to locate label text for Naukri form elements."""
    el_id = await element.get_attribute("id")
    if el_id:
        label = await page.query_selector(f"label[for='{el_id}']")
        if label:
            return (await label.inner_text()).strip()
            
    placeholder = await element.get_attribute("placeholder")
    if placeholder:
        return placeholder.strip()

    name = await element.get_attribute("name")
    if name:
        return name.replace("_", " ").replace("-", " ").strip()

    # Try parent label/text
    parent = await element.query_xpath("..")
    if parent:
        text = await parent[0].inner_text()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            return lines[0]
            
    return ""

async def apply_naukri_external(page: Page, job_id: str, dry_run: bool = True) -> bool:
    """
    Handles Naukri jobs that redirect to an external company website.
    Clicks the 'Apply on Company Site' button, captures the opened tab, and fills form.
    """
    print(f"[Naukri External] Navigating to job page for Job {job_id}...")
    
    apply_btn_selectors = [
        "#apply-button",
        ".apply-button",
        "button:has-text('Apply on Company Website')",
        "button:has-text('Apply')",
        "button.apply-button"
    ]
    
    apply_btn = None
    for selector in apply_btn_selectors:
        btn = await page.query_selector(selector)
        if btn and await btn.is_visible():
            apply_btn = btn
            break
            
    if not apply_btn:
        print("[Naukri External] Apply button not found on Naukri job page.")
        return False

    print("[Naukri External] Clicking Apply button to redirect to company site...")
    
    context = page.context
    try:
        async with context.expect_page(timeout=15000) as new_page_info:
            await apply_btn.click()
        external_page = await new_page_info.value
        await external_page.wait_for_load_state()
        print(f"[Naukri External] Redirected to company site: {external_page.url}")
        
        success = await apply_external_ats(external_page, external_page.url, dry_run=dry_run)
        await external_page.close()
        return success
    except Exception as e:
        print(f"[Naukri External] Error redirecting or filling external form: {e}")
        return False
