import os
from datetime import datetime, date
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import db

app = FastAPI(
    title="Auto Job Apply Dashboard",
    description="Railway-hosted monitoring portal for the LangGraph automated job agent."
)

# Setup templates directory
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request, filter_status: str = "all", filter_platform: str = "all"):
    conn = db.get_db_connection()
    cursor = conn.cursor()
    
    # Query all records
    cursor.execute("SELECT * FROM jobs ORDER BY scraped_at DESC, applied_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    # Process rows
    jobs_by_day = {}
    total_applied = 0
    total_skipped = 0
    total_failed = 0
    
    for r in rows:
        job = dict(r)
        
        # Increment counts
        if job["status"] == "applied":
            total_applied += 1
        elif job["status"] == "skipped":
            total_skipped += 1
        elif job["status"] == "failed":
            total_failed += 1
            
        # Extract date string YYYY-MM-DD
        ts = job.get("scraped_at") or job.get("applied_at")
        date_str = ts[:10] if ts else date.today().isoformat()
        
        # Apply filters
        if filter_status != "all" and job["status"] != filter_status:
            continue
        if filter_platform != "all" and job["platform"] != filter_platform:
            continue
            
        if date_str not in jobs_by_day:
            jobs_by_day[date_str] = []
        jobs_by_day[date_str].append(job)
        
    # Sort dates descending
    sorted_days = sorted(jobs_by_day.keys(), reverse=True)
    grouped_jobs = {day: jobs_by_day[day] for day in sorted_days}
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "grouped_jobs": grouped_jobs,
            "stats": {
                "total": len(rows),
                "applied": total_applied,
                "skipped": total_skipped,
                "failed": total_failed
            },
            "filters": {
                "status": filter_status,
                "platform": filter_platform
            }
        }
    )

if __name__ == "__main__":
    # Get port from environment (Railway standard)
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
