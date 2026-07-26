import sqlite3
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "jobs.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Jobs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT,
            location TEXT,
            apply_url TEXT,
            apply_type TEXT,
            scraped_at TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            applied_at TEXT
        )
    """)
    
    # Application Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS application_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        )
    """)
    
    conn.commit()
    conn.close()

def is_job_processed(job_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        # If already applied or skipped, it is processed. If failed, we might retry, or we can skip.
        # Let's count 'applied', 'skipped', 'failed' all as processed to avoid spamming.
        return row['status'] in ('applied', 'skipped', 'failed')
    return False

def add_job(job_id: str, platform: str, title: str, company: str, location: str, 
            apply_url: str, apply_type: str, status: str = 'scraped', reason: str = ''):
    conn = get_db_connection()
    cursor = conn.cursor()
    scraped_at = datetime.now().isoformat()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO jobs 
            (job_id, platform, title, company, location, apply_url, apply_type, scraped_at, status, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, platform, title, company, location, apply_url, apply_type, scraped_at, status, reason))
        conn.commit()
    except Exception as e:
        print(f"Error adding job to DB: {e}")
    finally:
        conn.close()

def update_job_status(job_id: str, status: str, reason: str = ''):
    conn = get_db_connection()
    cursor = conn.cursor()
    applied_at = datetime.now().isoformat() if status == 'applied' else None
    try:
        if status == 'applied':
            cursor.execute("""
                UPDATE jobs 
                SET status = ?, reason = ?, applied_at = ?
                WHERE job_id = ?
            """, (status, reason, applied_at, job_id))
        else:
            cursor.execute("""
                UPDATE jobs 
                SET status = ?, reason = ?
                WHERE job_id = ?
            """, (status, reason, job_id))
        
        # Log action
        cursor.execute("""
            INSERT INTO application_logs (job_id, timestamp, action, details)
            VALUES (?, ?, ?, ?)
        """, (job_id, datetime.now().isoformat(), status, reason))
        
        conn.commit()
    except Exception as e:
        print(f"Error updating job status: {e}")
    finally:
        conn.close()

def get_applied_count_today() -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    today_start = date.today().isoformat()
    cursor.execute("""
        SELECT COUNT(*) FROM jobs 
        WHERE status = 'applied' AND applied_at >= ?
    """, (today_start,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def clear_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM application_logs")
        cursor.execute("DELETE FROM jobs")
        conn.commit()
        print("[Database] Cleared all job and application records.")
    except Exception as e:
        print(f"[Database] Error clearing DB: {e}")
    finally:
        conn.close()

# Initialize on import
init_db()
