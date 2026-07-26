import urllib.parse
import re
from playwright.async_api import BrowserContext
from browser_manager import browser_manager
import db

async def scrape_linkedin_jobs(context: BrowserContext, keyword: str, location: str):
    page = await context.new_page()
    encoded_keyword = urllib.parse.quote(keyword)
    encoded_location = urllib.parse.quote(location)
    
    # f_TPR=r86400 filters jobs posted in the past 24 hours
    search_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded_keyword}&location={encoded_location}&f_TPR=r86400"
    
    print(f"[LinkedIn Scraper] Navigating to {search_url}")
    try:
        await page.goto(search_url)
        await browser_manager.human_delay(4.0, 6.0)
    except Exception as e:
        print(f"[LinkedIn Scraper] Navigation failed: {e}")
        await page.close()
        return

    # Check for authentication redirect
    current_url = page.url
    if "login" in current_url or "signup" in current_url:
        print("[LinkedIn Scraper] Error: Not logged in. Run `python main.py --login` first.")
        await page.close()
        return

    # Wait for the search results container
    list_selector = '.jobs-search-results-list, .scaffold-layout__list, [data-launchpad-scrollable]'
    try:
        await page.wait_for_selector(list_selector, timeout=15000)
    except Exception:
        # Check if we got zero results page
        if await page.query_selector(".jobs-search-two-pane__no-results-banner"):
            print(f"[LinkedIn Scraper] No results found for '{keyword}' in '{location}'.")
        else:
            print("[LinkedIn Scraper] Job list selector not found. Page layout may be different.")
        await page.close()
        return

    print("[LinkedIn Scraper] Job list loaded. Scrolling list to lazy-load more job cards...")
    # Scroll list container to load more jobs
    for _ in range(4):
        await page.evaluate(f"""
            const el = document.querySelector('{list_selector}');
            if (el) el.scrollTop += 800;
        """)
        await browser_manager.human_delay(1.5, 2.5)

    # Find job cards
    card_selectors = [
        '.job-card-container[data-job-id]',
        'li[data-occludable-job-id]',
        '.jobs-search-results__list-item',
        '[data-job-id]'
    ]
    
    cards = []
    for selector in card_selectors:
        elements = await page.query_selector_all(selector)
        if elements and len(elements) > 0:
            cards = elements
            print(f"[LinkedIn Scraper] Found {len(cards)} job cards with selector: '{selector}'")
            break

    if not cards:
        print("[LinkedIn Scraper] No job cards found.")
        await page.close()
        return

    scraped_count = 0
    # Process up to 15 cards
    for idx, card in enumerate(cards[:15]):
        try:
            # Scroll to card
            await card.scroll_into_view_if_needed()
            await browser_manager.human_delay(0.5, 1.0)
            
            # Extract job ID
            job_id = None
            for attr in ['data-job-id', 'data-occludable-job-id']:
                val = await card.get_attribute(attr)
                if val:
                    job_id = val.strip()
                    break
            
            if not job_id:
                # Try finding sub-elements with link
                link_el = await card.query_selector('a.job-card-container__link, a.job-card-list__title, a[href*="/jobs/view/"]')
                if link_el:
                    href = await link_el.get_attribute('href')
                    if href:
                        match = re.search(r'/view/(\d+)', href)
                        if match:
                            job_id = match.group(1)

            if not job_id:
                print(f"[LinkedIn Scraper] Skipping card {idx}: Could not find job ID.")
                continue

            if db.is_job_processed(job_id):
                print(f"[LinkedIn Scraper] Job {job_id} already in DB and marked processed. Skipping detail fetch.")
                continue

            # Click card to open detail pane
            await card.click()
            await browser_manager.human_delay(1.5, 3.0)

            # Extract job details from details pane
            title = ""
            for selector in ['.job-details-jobs-unified-top-card__job-title', 'h1.t-24', '.jobs-unified-top-card__job-title', 'h2.jobs-details-panel__title']:
                el = await page.query_selector(selector)
                if el:
                    title = (await el.inner_text()).strip()
                    break

            company = ""
            for selector in ['.job-details-jobs-unified-top-card__company-name a', '.jobs-unified-top-card__company-name', '.jobs-details-panel__company-name']:
                el = await page.query_selector(selector)
                if el:
                    company = (await el.inner_text()).strip()
                    break

            location = ""
            for selector in ['.job-details-jobs-unified-top-card__bullet', '.jobs-unified-top-card__bullet', '.jobs-details-panel__bullet']:
                el = await page.query_selector(selector)
                if el:
                    location = (await el.inner_text()).strip()
                    break

            description = ""
            for selector in ['#job-details', '.jobs-description__content', '.jobs-description', 'article.jobs-description', '.jobs-box__html-content']:
                el = await page.query_selector(selector)
                if el:
                    description = (await el.inner_text()).strip()
                    break

            if not title or not description:
                print(f"[LinkedIn Scraper] Failed to extract title or description for job {job_id}. Skipping.")
                continue

            # Check for Easy Apply
            easy_apply_btn = await page.query_selector("button:has-text('Easy Apply'), button.jobs-apply-button:has-text('Easy Apply')")
            apply_type = "easy_apply" if easy_apply_btn else "external"

            apply_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
            if apply_type == "external":
                # Find external apply link
                ext_el = await page.query_selector('a.jobs-apply-button[href], a[data-tracking-control-name*="apply"][href]')
                if ext_el:
                    apply_url = await ext_el.get_attribute('href')

            db.add_job(
                job_id=job_id,
                platform="linkedin",
                title=title,
                company=company,
                location=location,
                apply_url=apply_url,
                apply_type=apply_type,
                status="scraped"
            )
            print(f"[LinkedIn Scraper] Successfully scraped: {title} @ {company} | Type: {apply_type}")
            scraped_count += 1
            
        except Exception as e:
            print(f"[LinkedIn Scraper] Error scraping card {idx}: {e}")

    print(f"[LinkedIn Scraper] Scrape round completed. Scraped {scraped_count} new jobs.")
    await page.close()
