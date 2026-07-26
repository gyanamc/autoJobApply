import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    
    # LLM Settings
    LLM_PROVIDER = "openai" if OPENAI_API_KEY else ("groq" if os.getenv("GROQ_API_KEY") else "mock")
    OPENAI_MODEL = "gpt-4o-mini"
    GROQ_MODEL = "llama-3.1-70b-versatile"
    
    # Search Configurations
    SEARCH_KEYWORDS = [k.strip() for k in os.getenv("SEARCH_KEYWORDS", "Chief AI Architect, Generative AI").split(",")]
    SEARCH_LOCATIONS = [l.strip() for l in os.getenv("SEARCH_LOCATIONS", "Gurgaon, Noida, Remote").split(",")]
    
    # Browser persistent profile
    CHROME_USER_DATA_DIR = os.getenv("CHROME_USER_DATA_DIR", "./browser_session")
    
    # Security/Delay limits
    MAX_DAILY_APPLICATIONS = int(os.getenv("MAX_DAILY_APPLICATIONS", "10"))
    HUMAN_DELAY_MIN = int(os.getenv("HUMAN_DELAY_MIN", "2"))
    HUMAN_DELAY_MAX = int(os.getenv("HUMAN_DELAY_MAX", "5"))
    
    # Resume Paths
    RESUME_PDF_PATH = BASE_DIR / "resume.pdf"
    RESUME_TXT_PATH = BASE_DIR / "resume_data.txt"

config = Config()
