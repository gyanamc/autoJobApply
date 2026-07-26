import asyncio
import argparse
import sys
from datetime import datetime
from playwright.async_api import async_playwright
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from browser_manager import browser_manager
from config import config
from agent_graph import app_graph
import db

async def run_login_session():
    """
    Opens headed browser tabs for LinkedIn and Naukri.com, 
    allowing the user to log in manually and resolve 2FA/CAPTCHAs.
    """
    print("\n==============================================")
    print("      MANUAL LOGIN & SESSION SAVER MODE")
    print("==============================================")
    print("Opening Chromium in headed mode...")
    print("Please log in to LinkedIn and Naukri.com in the browser tabs.")
    print(f"Your session will be saved to: {config.CHROME_USER_DATA_DIR}")
    
    async with async_playwright() as p:
        context = await browser_manager.get_browser_context(p, headed=True)
        
        # Open LinkedIn
        page1 = await context.new_page()
        print("\nOpening LinkedIn login...")
        await page1.goto("https://www.linkedin.com/login")
        
        # Open Naukri
        page2 = await context.new_page()
        print("Opening Naukri login...")
        await page2.goto("https://www.naukri.com/nlogin/login")
        
        # Wait for user input in CLI to exit
        print("\n--> INSTRUCTIONS:")
        print("1. Log in manually on both tabs.")
        print("2. Solve any 2FA, phone verification, or CAPTCHAs.")
        print("3. Verify you can see your homepage/feed.")
        print("4. Come back to this terminal and press ENTER to save session and exit.")
        
        # Run input in a thread since standard input blocks async loops
        await asyncio.get_event_loop().run_in_executor(None, input, "Press ENTER when finished...")
        
        # Save storage state to state.json
        state_path = Path(__file__).resolve().parent / "state.json"
        await context.storage_state(path=str(state_path))
        print(f"\n[Session] Storage state (cookies + local storage) successfully saved to: {state_path}")
        
        # Close pages and context to write cookies to disk
        await page1.close()
        await page2.close()
        await context.close()
        
    print("\nSession saved successfully! You can now run in automated mode.")

async def run_agent_workflow(dry_run: bool = True, headed: bool = False):
    """Runs the LangGraph agent once to scrape, evaluate, and apply for jobs."""
    run_start_time = datetime.now().isoformat()
    print("\n==============================================")
    print(f"    RUNNING JOB APPLY AGENT (Dry Run: {dry_run})")
    print("==============================================")
    print(f"Current local time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Check if resume.pdf exists or if we have resume text
    if not config.RESUME_PDF_PATH.exists():
        print(f"\n[WARNING] resume.pdf not found in the workspace root ({config.RESUME_PDF_PATH}).")
        print("The agent will skip uploading a resume file, which may cause applications to fail.")
        print("Please place your 'resume.pdf' in the workspace directory.\n")
        
    # Check today's application count to respect rate limits
    today_applied = db.get_applied_count_today()
    if today_applied >= config.MAX_DAILY_APPLICATIONS:
        print(f"[Limit] Already applied to {today_applied} jobs today (Limit: {config.MAX_DAILY_APPLICATIONS}). Exiting.")
        return

    async with async_playwright() as p:
        # Load the persistent context
        context = await browser_manager.get_browser_context(p, headed=headed)
        
        initial_state = {
            "keywords": config.SEARCH_KEYWORDS,
            "locations": config.SEARCH_LOCATIONS,
            "current_keyword_idx": 0,
            "current_location_idx": 0,
            "jobs": [],
            "current_job_idx": 0,
            "applied_count": today_applied,
            "skipped_count": 0,
            "failed_count": 0,
            "dry_run": dry_run
        }
        
        # Execute LangGraph
        print("[Agent Core] Launching LangGraph workflow...")
        try:
            async for event in app_graph.astream(
                initial_state,
                {"configurable": {"browser_context": context}},
                stream_mode="values"
            ):
                # Print state transitions
                curr_job = event.get("current_job_idx", 0)
                total_jobs = len(event.get("jobs", []))
                if total_jobs > 0:
                    print(f"Progress: Job {curr_job}/{total_jobs} | Applied: {event.get('applied_count', 0)} | Skipped: {event.get('skipped_count', 0)}")
        except Exception as graph_err:
            print(f"[Agent Core] Graph Execution Error: {graph_err}")
        finally:
            await context.close()

    # Fetch run statistics for the dashboard
    try:
        conn = db.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(db._qp("""
            SELECT title, company, platform, status, reason, apply_url, applied_at, scraped_at, apply_type 
            FROM jobs
            WHERE scraped_at >= ? OR applied_at >= ?
        """), (run_start_time, run_start_time))
        rows = cursor.fetchall()
        conn.close()
    except Exception as db_err:
        print(f"[Dashboard] Error fetching stats from DB: {db_err}")
        rows = []

    applied_jobs = []
    skipped_jobs = []
    failed_jobs = []
    for r in rows:
        job = dict(r)
        if job["status"] == "applied":
            applied_jobs.append(job)
        elif job["status"] == "skipped":
            skipped_jobs.append(job)
        elif job["status"] == "failed":
            failed_jobs.append(job)

    # CLI Terminal Dashboard
    print("\n" + "="*60)
    print("                EXECUTION RUN DASHBOARD")
    print("="*60)
    print(f"Total Jobs Evaluated: {len(rows)}")
    print(f"  - Applied (Success): {len(applied_jobs)}")
    print(f"  - Skipped (Mismatch): {len(skipped_jobs)}")
    print(f"  - Failed (Error): {len(failed_jobs)}")
    print("-"*60)
    
    if applied_jobs:
        print("\n--- APPLIED JOBS (SUBMITTED) ---")
        for job in applied_jobs:
            print(f"✅ {job['title']} at {job['company']} ({job['platform'].upper()} - {job['apply_type']})")
            
    if failed_jobs:
        print("\n--- FAILED APPLICATIONS ---")
        for job in failed_jobs:
            print(f"❌ {job['title']} at {job['company']} | Error: {job['reason']}")
            
    if skipped_jobs:
        print("\n--- SKIPPED JOBS ---")
        for job in skipped_jobs:
            reason = job['reason'] or ""
            if len(reason) > 85:
                reason = reason[:82] + "..."
            print(f"➖ {job['title']} at {job['company']} | Reason: {reason}")
    print("="*60)

    # Write Markdown Dashboard
    dashboard_path = "latest_run_dashboard.md"
    try:
        with open(dashboard_path, "w") as md_file:
            md_file.write(f"# Latest Agent Run Dashboard\n\n")
            md_file.write(f"**Execution Start Time**: {datetime.fromisoformat(run_start_time).strftime('%Y-%m-%d %H:%M:%S')}\n")
            md_file.write(f"**Execution Mode**: {'Dry Run (Simulation)' if dry_run else 'Real Application Submission'}\n\n")
            
            md_file.write("## Run Metrics\n\n")
            md_file.write(f"- **Total Scraped/Evaluated**: {len(rows)}\n")
            md_file.write(f"- **Applied**: {len(applied_jobs)}\n")
            md_file.write(f"- **Skipped**: {len(skipped_jobs)}\n")
            md_file.write(f"- **Failed**: {len(failed_jobs)}\n\n")
            
            md_file.write("## Detailed Run Logs\n\n")
            if applied_jobs:
                md_file.write("### ✅ Applied Jobs\n\n")
                md_file.write("| Job Title | Company | Platform | Type | Apply Link |\n")
                md_file.write("| :--- | :--- | :--- | :--- | :--- |\n")
                for job in applied_jobs:
                    md_file.write(f"| {job['title']} | {job['company']} | {job['platform'].upper()} | {job['apply_type']} | [Link]({job['apply_url']}) |\n")
                md_file.write("\n")
                
            if failed_jobs:
                md_file.write("### ❌ Failed Applications\n\n")
                md_file.write("| Job Title | Company | Error / Failure Reason |\n")
                md_file.write("| :--- | :--- | :--- |\n")
                for job in failed_jobs:
                    md_file.write(f"| {job['title']} | {job['company']} | {job['reason']} |\n")
                md_file.write("\n")
                
            if skipped_jobs:
                md_file.write("### ➖ Skipped Jobs (Evaluated Mismatch)\n\n")
                md_file.write("| Job Title | Company | Match Evaluation Reason |\n")
                md_file.write("| :--- | :--- | :--- |\n")
                for job in skipped_jobs:
                    md_file.write(f"| {job['title']} | {job['company']} | {job['reason']} |\n")
                md_file.write("\n")
                
        print(f"[Dashboard] Detailed Markdown Dashboard written to: [latest_run_dashboard.md](file:///Users/kumargyanam/Downloads/autoJobApply/latest_run_dashboard.md)\n")
    except Exception as md_err:
        print(f"[Dashboard] Error writing markdown file: {md_err}")

def main():
    parser = argparse.ArgumentParser(description="LangGraph Automated Job Application Agent")
    parser.add_argument("--login", action="store_true", help="Open headed browser to log in and save session")
    parser.add_argument("--run", action="store_true", help="Run the application agent workflow immediately")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Submit actual job applications (default: dry run)")
    parser.add_argument("--headed", action="store_true", help="Run automation in headed mode (visible browser)")
    parser.add_argument("--schedule", action="store_true", help="Start background scheduler for daily execution")
    parser.add_argument("--time", default="09:00", help="Time of day to run the scheduler (HH:MM), default 09:00")
    parser.add_argument("--fresh", action="store_true", help="Wipe local database to start a completely fresh scraping run")
    
    parser.set_defaults(dry_run=True)
    args = parser.parse_args()

    if args.fresh:
        db.clear_db()

    if args.login:
        asyncio.run(run_login_session())
    elif args.run:
        asyncio.run(run_agent_workflow(dry_run=args.dry_run, headed=args.headed))
    elif args.schedule:
        scheduler = AsyncIOScheduler()
        hour, minute = map(int, args.time.split(":"))
        
        # Schedule the job to run daily
        scheduler.add_job(
            run_agent_workflow,
            "cron",
            hour=hour,
            minute=minute,
            kwargs={"dry_run": args.dry_run, "headed": args.headed}
        )
        
        print(f"\n[Scheduler] Started. Agent will run daily at {args.time} (Dry Run: {args.dry_run}).")
        print("Press Ctrl+C to stop the scheduler.")
        
        scheduler.start()
        try:
            asyncio.get_event_loop().run_forever()
        except (KeyboardInterrupt, SystemExit):
            print("\n[Scheduler] Stopped.")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
