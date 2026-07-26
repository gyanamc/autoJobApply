from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from playwright.async_api import BrowserContext
from langchain_core.runnables import RunnableConfig
import db
from config import config as app_config
from linkedin_scraper import scrape_linkedin_jobs
from naukri_scraper import scrape_naukri_jobs
from form_filler_agent import form_filler_agent
from linkedin_easy_apply import apply_linkedin_easy_apply
from external_form_filler import apply_external_ats
from naukri_easy_apply import apply_naukri_easy_apply, apply_naukri_external

# State Definition
class AgentState(TypedDict):
    keywords: List[str]
    locations: List[str]
    current_keyword_idx: int
    current_location_idx: int
    jobs: List[Dict[str, Any]]
    current_job_idx: int
    applied_count: int
    skipped_count: int
    failed_count: int
    dry_run: bool
    finished: bool

# Node 1: Scrape Jobs
async def scrape_jobs_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    keywords = state["keywords"]
    locations = state["locations"]
    k_idx = state["current_keyword_idx"]
    l_idx = state["current_location_idx"]
    
    if k_idx >= len(keywords) or l_idx >= len(locations):
        return {"finished": True}

    keyword = keywords[k_idx]
    location = locations[l_idx]
    
    print(f"\n--- [Graph Node] Scraping for '{keyword}' in '{location}' ---")
    
    # Retrieve Playwright Context from config
    context: BrowserContext = config["configurable"]["browser_context"]
    
    # Run scrapers
    try:
        await scrape_linkedin_jobs(context, keyword, location)
    except Exception as e:
        print(f"[Graph Node] Error scraping LinkedIn: {e}")
        
    try:
        await scrape_naukri_jobs(context, keyword, location)
    except Exception as e:
        print(f"[Graph Node] Error scraping Naukri: {e}")

    # Fetch newly scraped jobs from SQLite DB
    conn = db.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT job_id, platform, title, company, location, apply_url, apply_type, status, reason
        FROM jobs 
        WHERE status = 'scraped'
    """)
    rows = cursor.fetchall()
    conn.close()
    
    scraped_jobs = [dict(r) for r in rows]
    print(f"[Graph Node] Found {len(scraped_jobs)} unprocessed jobs in local DB.")
    
    return {
        "jobs": scraped_jobs,
        "current_job_idx": 0,
        "finished": False
    }

# Node 2: Evaluate Job
async def evaluate_job_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    jobs = state["jobs"]
    idx = state["current_job_idx"]
    
    if idx >= len(jobs):
        return {}
        
    job = jobs[idx]
    print(f"\n--- [Graph Node] Evaluating Job {idx+1}/{len(jobs)}: {job['title']} at {job['company']} ---")
    
    # Check if we exceeded daily limit
    if state["applied_count"] >= app_config.MAX_DAILY_APPLICATIONS:
        print("[Graph Node] Daily application limit reached. Skipping evaluation.")
        db.update_job_status(job["job_id"], "skipped", "Daily limit reached")
        return {
            "skipped_count": state["skipped_count"] + 1,
            "current_job_idx": idx + 1
        }
        
    # Evaluate via LLM Form Filler Agent
    evaluation = await form_filler_agent.evaluate_job_match(
        title=job["title"],
        company=job["company"],
        description=job.get("reason", "") + "\n" + job["title"]
    )
    
    print(f"[Graph Node] Match Status: {evaluation.is_match} | Score: {evaluation.match_score}")
    print(f"[Graph Node] Reasoning: {evaluation.reasoning}")
    
    if evaluation.is_match:
        # Mark as matched so apply node knows it should execute
        jobs[idx]["status"] = "matched"
        return {
            "jobs": jobs
        }
    else:
        db.update_job_status(job["job_id"], "skipped", f"Score {evaluation.match_score}: {evaluation.reasoning}")
        return {
            "skipped_count": state["skipped_count"] + 1,
            "current_job_idx": idx + 1
        }

# Node 3: Apply Job
async def apply_job_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    jobs = state["jobs"]
    idx = state["current_job_idx"]
    
    if idx >= len(jobs):
        return {}
        
    job = jobs[idx]
    
    if job.get("status") != "matched":
        return {"current_job_idx": idx + 1}
        
    print(f"\n--- [Graph Node] Applying to Job: {job['title']} at {job['company']} ({job['apply_type']}) ---")
    
    context: BrowserContext = config["configurable"]["browser_context"]
    page = await context.new_page()
    
    success = False
    error_msg = ""
    
    try:
        if job["platform"] == "linkedin":
            if job["apply_type"] == "easy_apply":
                await page.goto(job["apply_url"])
                success = await apply_linkedin_easy_apply(page, job["job_id"], dry_run=state["dry_run"])
            else:
                success = await apply_external_ats(page, job["apply_url"], dry_run=state["dry_run"])
        elif job["platform"] == "naukri":
            if job["apply_type"] == "easy_apply":
                await page.goto(job["apply_url"])
                success = await apply_naukri_easy_apply(page, job["job_id"], dry_run=state["dry_run"])
            else:
                await page.goto(job["apply_url"])
                success = await apply_naukri_external(page, job["job_id"], dry_run=state["dry_run"])
        else:
            success = await apply_external_ats(page, job["apply_url"], dry_run=state["dry_run"])
            
    except Exception as apply_err:
        error_msg = str(apply_err)
        print(f"[Graph Node] Application Exception: {apply_err}")
        success = False
    finally:
        await page.close()
        
    if success:
        status_label = "applied"
        reason_label = "Dry run completed successfully" if state["dry_run"] else "Application submitted"
        applied_inc = 1
        failed_inc = 0
        print(f"[Graph Node] ✅ Application Success (Dry Run: {state['dry_run']}) for {job['title']}")
    else:
        status_label = "failed"
        reason_label = f"Automation failed: {error_msg}"
        applied_inc = 0
        failed_inc = 1
        print(f"[Graph Node] ❌ Application Failed for {job['title']}")
        
    db.update_job_status(job["job_id"], status_label, reason_label)
    
    return {
        "applied_count": state["applied_count"] + applied_inc,
        "failed_count": state["failed_count"] + failed_inc,
        "current_job_idx": idx + 1
    }

# Node 4: Check Next / Advance Search
async def check_next_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    jobs = state["jobs"]
    idx = state["current_job_idx"]
    
    # If there are still jobs in the list to evaluate/apply
    if idx < len(jobs):
        return {}
        
    # All jobs for current keyword/location processed. Try to advance search parameters.
    k_idx = state["current_keyword_idx"]
    l_idx = state["current_location_idx"]
    keywords = state["keywords"]
    locations = state["locations"]
    
    # Try next location
    if l_idx + 1 < len(locations):
        print(f"[Graph Node] Advancing to next location: {locations[l_idx + 1]}")
        return {
            "current_location_idx": l_idx + 1,
            "jobs": [],
            "current_job_idx": 0,
            "finished": False
        }
    # Try next keyword
    elif k_idx + 1 < len(keywords):
        print(f"[Graph Node] Advancing to next keyword: {keywords[k_idx + 1]}")
        return {
            "current_keyword_idx": k_idx + 1,
            "current_location_idx": 0,
            "jobs": [],
            "current_job_idx": 0,
            "finished": False
        }
    else:
        print("[Graph Node] All search keywords and locations exhausted.")
        return {"finished": True}

# Routing Functions
def route_after_scrape(state: AgentState) -> str:
    if len(state["jobs"]) > 0:
        return "evaluate_job"
    return "check_next"

def route_after_evaluate(state: AgentState) -> str:
    jobs = state["jobs"]
    idx = state["current_job_idx"]
    if idx < len(jobs):
        job = jobs[idx]
        if job.get("status") == "matched":
            return "apply_job"
    return "check_next"

def route_after_check_next(state: AgentState) -> str:
    if state["finished"]:
        return END
    if len(state["jobs"]) == 0:
        return "scrape_jobs"
    return "evaluate_job"

# Build the Graph
workflow = StateGraph(AgentState)

workflow.add_node("scrape_jobs", scrape_jobs_node)
workflow.add_node("evaluate_job", evaluate_job_node)
workflow.add_node("apply_job", apply_job_node)
workflow.add_node("check_next", check_next_node)

workflow.set_entry_point("scrape_jobs")

workflow.add_conditional_edges("scrape_jobs", route_after_scrape, {
    "evaluate_job": "evaluate_job",
    "check_next": "check_next"
})

workflow.add_conditional_edges("evaluate_job", route_after_evaluate, {
    "apply_job": "apply_job",
    "check_next": "check_next"
})

workflow.add_edge("apply_job", "check_next")

workflow.add_conditional_edges("check_next", route_after_check_next, {
    "scrape_jobs": "scrape_jobs",
    "evaluate_job": "evaluate_job",
    END: END
})

app_graph = workflow.compile()
