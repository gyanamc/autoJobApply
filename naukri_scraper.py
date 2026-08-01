import urllib.parse
from playwright.async_api import BrowserContext
from browser_manager import browser_manager
import db

async def scrape_naukri_jobs(context: BrowserContext, keyword: str, location: str):
    page = await context.new_page()
    
    # Construct search URL
    # E.g. https://www.naukri.com/jobs-in-gurgaon?k=Generative%20AI
    loc_clean = location.lower().replace(" ", "-")
    keyword_encoded = urllib.parse.quote(keyword)
    search_url = f"https://www.naukri.com/jobs-in-{loc_clean}?k={keyword_encoded}"
    
    print(f"[Naukri Scraper] Navigating to {search_url}")
    try:
        await page.goto(search_url)
        await browser_manager.human_delay(4.0, 6.0)
    except Exception as e:
        print(f"[Naukri Scraper] Navigation failed: {e}")
        await page.close()
        return

    # Check for authentication or bot detection redirects
    if "login" in page.url or "captcha" in page.url:
        print("[Naukri Scraper] Error: Logged out or CAPTCHA detected. Run `python main.py --login` first.")
        await page.close()
        return

    # Wait for job wrappers
    try:
        await page.wait_for_selector(".srp-jobtuple-wrapper", timeout=15000)
    except Exception:
        print("[Naukri Scraper] Job wrappers not found. Maybe no results or different layout.")
        print(f"[Naukri Scraper] Active Page URL: {page.url}")
        print(f"[Naukri Scraper] Active Page Title: {await page.title()}")
        await page.close()
        return

    # Scroll page to load all visible cards
    await browser_manager.scroll_page_randomly(page, distance_range=(400, 800), steps=3)

    cards = await page.query_selector_all(".srp-jobtuple-wrapper")
    print(f"[Naukri Scraper] Found {len(cards)} job cards on page.")

    scraped_count = 0
    for idx, card in enumerate(cards[:15]):
        try:
            # Extract basic details
            title_el = await card.query_selector("a.title, a.job-title")
            if not title_el:
                continue
                
            title = (await title_el.inner_text()).strip()
            apply_url = await title_el.get_attribute("href")
            if not apply_url:
                continue

            # Extract Unique Job ID from Naukri URL or use URL itself
            # E.g., URL contains job ID like "...-job-123456789..."
            # Let's extract job ID using regex or use URL as fallback ID
            import re
            job_id = None
            match = re.search(r"-(\d{12,})", apply_url) # Naukri job IDs are usually 12 digits or more
            if match:
                job_id = match.group(1)
            else:
                # Use clean URL hash or raw URL if no match
                import hashlib
                job_id = "naukri_" + hashlib.md5(apply_url.encode()).hexdigest()[:16]

            if db.is_job_processed(job_id):
                print(f"[Naukri Scraper] Job {job_id} already processed. Skipping.")
                continue

            company_el = await card.query_selector("a.comp-name, .company_name")
            company = (await company_el.inner_text()).strip() if company_el else "Unknown"

            location_el = await card.query_selector(".loc-wrap, .location, .locWdth")
            location_str = (await location_el.inner_text()).strip() if location_el else location

            salary_el = await card.query_selector(".sal-wrap, .salary, .salaryWdth")
            salary_str = (await salary_el.inner_text()).strip() if salary_el else "Not Disclosed"

            snippet_el = await card.query_selector(".job-desc, .jobDescription")
            snippet = (await snippet_el.inner_text()).strip() if snippet_el else ""

            skill_els = await card.query_selector_all(".dot-wrapper li, .tag-li, .chip")
            skills = []
            for s in skill_els:
                skills.append((await s.inner_text()).strip())
            skills_str = ", ".join(skills)

            # Let's navigate to the job detail page in a new page/tab or extract more if needed.
            # Usually Naukri search cards contain 80% of what we need. Let's do detail extraction
            # by opening the job URL to get the full description, because matching requires full description.
            detail_page = await context.new_page()
            try:
                print(f"[Naukri Scraper] Fetching job details from {apply_url}")
                await detail_page.goto(apply_url)
                await browser_manager.human_delay(2.0, 4.0)
                
                # Check for description element on detail page
                # Naukri details container could be ".jd-desc" or ".job-desc"
                desc_el = await detail_page.query_selector(".jd-desc, .job-desc, #job-description, .description")
                if desc_el:
                    full_desc = (await desc_el.inner_text()).strip()
                else:
                    full_desc = f"{snippet}\nKey Skills: {skills_str}"
                    
                # Check if it redirects to external website
                # Internal apply buttons usually say "Apply" or "Apply on Company Site"
                # Let's inspect apply button
                apply_btn = await detail_page.query_selector("#apply-button, .apply-button, button:has-text('Apply')")
                apply_type = "easy_apply" # Default is internal naukri apply
                
                if apply_btn:
                    btn_text = await apply_btn.inner_text()
                    if "company website" in btn_text.lower() or "apply on company" in btn_text.lower():
                        apply_type = "external"
            except Exception as detail_err:
                print(f"[Naukri Scraper] Error fetching detail page for job {job_id}: {detail_err}")
                full_desc = f"{snippet}\nKey Skills: {skills_str}"
                apply_type = "easy_apply"
            finally:
                await detail_page.close()

            db.add_job(
                job_id=job_id,
                platform="naukri",
                title=title,
                company=company,
                location=location_str,
                apply_url=apply_url,
                apply_type=apply_type,
                status="scraped",
                reason=f"Salary: {salary_str}"
            )
            print(f"[Naukri Scraper] Successfully scraped: {title} @ {company} | Type: {apply_type}")
            scraped_count += 1

        except Exception as e:
            print(f"[Naukri Scraper] Error scraping card {idx}: {e}")

    print(f"[Naukri Scraper] Scrape round completed. Scraped {scraped_count} new jobs.")
    await page.close()
