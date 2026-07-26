import json
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from config import config

class JobEvaluation(BaseModel):
    is_match: bool = Field(description="Whether this job matches the candidate's profile.")
    match_score: int = Field(description="Score between 0 and 100 representing how well the candidate matches the job.")
    reasoning: str = Field(description="1-2 sentences explaining why it is or is not a match.")

class FormAnswer(BaseModel):
    answer: str = Field(description="The textual answer, selected choice, or numeric value for the question.")

class FormFillerAgent:
    def __init__(self):
        self._load_resume()
        self._init_llm()

    def _load_resume(self):
        self.resume_text = ""
        # 1. Try reading pre-parsed text
        if Path(config.RESUME_TXT_PATH).exists():
            with open(config.RESUME_TXT_PATH, "r") as f:
                self.resume_text = f.read().strip()
                
        # 2. Try parsing PDF directly if text not available
        elif Path(config.RESUME_PDF_PATH).exists():
            print("[FormFillerAgent] Parsing resume.pdf using pdfplumber...")
            try:
                import pdfplumber
                with pdfplumber.open(config.RESUME_PDF_PATH) as pdf:
                    pages = [page.extract_text() or "" for page in pdf.pages]
                    self.resume_text = "\n".join(pages).strip()
                # Save parsed text for caching
                with open(config.RESUME_TXT_PATH, "w") as f:
                    f.write(self.resume_text)
            except Exception as e:
                print(f"[FormFillerAgent] Error parsing resume.pdf: {e}")
                
        if not self.resume_text:
            print("[FormFillerAgent] Warning: No resume data found. LLM queries might fail or yield poor results.")

    def _init_llm(self):
        if config.LLM_PROVIDER == "openai":
            print(f"[FormFillerAgent] Initializing OpenAI Chat model: {config.OPENAI_MODEL}")
            self.llm = ChatOpenAI(
                model=config.OPENAI_MODEL,
                temperature=0.0,
                openai_api_key=config.OPENAI_API_KEY
            )
        elif config.LLM_PROVIDER == "groq":
            print(f"[FormFillerAgent] Initializing Groq Chat model: {config.GROQ_MODEL}")
            self.llm = ChatGroq(
                model=config.GROQ_MODEL,
                temperature=0.0,
                groq_api_key=config.GROQ_API_KEY
            )
        else:
            print("[FormFillerAgent] Warning: No LLM key configured. Running in Mock Mode.")
            self.llm = None

    async def evaluate_job_match(self, title: str, company: str, description: str) -> JobEvaluation:
        """
        Evaluate if the job matches the candidate's resume.
        """
        if not self.llm:
            # Mock evaluation for testing
            is_match = any(kw.lower() in title.lower() or kw.lower() in description.lower() for kw in config.SEARCH_KEYWORDS)
            return JobEvaluation(
                is_match=is_match,
                match_score=85 if is_match else 30,
                reasoning="Mock Evaluation: Matched search keywords." if is_match else "Mock Evaluation: No keyword match."
            )

        parser = JsonOutputParser(pydantic_object=JobEvaluation)
        
        prompt = ChatPromptTemplate.from_template(
            "You are an AI Job Matching Assistant. Evaluate if this job is a good fit for the candidate based on their resume.\n\n"
            "Candidate Resume:\n{resume}\n\n"
            "Job Title: {title}\n"
            "Company: {company}\n"
            "Job Description:\n{description}\n\n"
            "Evaluate strictly based on the candidate's skills, seniority level, and target roles.\n"
            "Format the output strictly as JSON with the following schema:\n{format_instructions}"
        )

        chain = prompt | self.llm | parser
        
        try:
            result = await chain.ainvoke({
                "resume": self.resume_text,
                "title": title,
                "company": company,
                "description": description[:3000],  # Truncate to avoid token limits
                "format_instructions": parser.get_format_instructions()
            })
            return JobEvaluation(**result)
        except Exception as e:
            print(f"[FormFillerAgent] Error in job evaluation LLM call: {e}")
            # Safe fallback
            return JobEvaluation(is_match=False, match_score=0, reasoning=f"Error evaluating job: {str(e)}")

    async def answer_form_question(self, question_text: str, field_type: str = "text", choices: list = None) -> str:
        """
        Answers a form question based on the resume.
        """
        if not self.llm:
            # Basic mock answers
            q_lower = question_text.lower()
            if "first name" in q_lower or "given name" in q_lower:
                return "Kumar"
            elif "last name" in q_lower or "family name" in q_lower:
                return "Gyanam"
            elif "email" in q_lower:
                return "gyanamc@gmail.com"
            elif "phone" in q_lower or "mobile" in q_lower:
                return "+91 9953682525"
            elif "linkedin" in q_lower:
                return "https://linkedin.com/in/kumar-gyanam"
            elif "github" in q_lower:
                return "https://github.com/gyanamc"
            elif "experience" in q_lower or "years" in q_lower:
                return "20"
            return "Please refer to the attached resume."

        parser = JsonOutputParser(pydantic_object=FormAnswer)
        
        choices_str = f"Choices: {choices}\n" if choices else ""
        
        prompt = ChatPromptTemplate.from_template(
            "You are an AI Assistant helping a candidate fill out a job application form. Answer the question based on the candidate's resume.\n\n"
            "Candidate Resume:\n{resume}\n\n"
            "Question: {question_text}\n"
            "Field Type: {field_type}\n"
            "{choices_str}"
            "Rules:\n"
            "1. Give a truthful answer based ONLY on the candidate's resume.\n"
            "2. If the field is numeric (e.g. years of experience), return a clean integer (e.g. 5, 20).\n"
            "3. If choices are provided, select the single best matching option from the choices. Do not return anything else.\n"
            "4. Keep answer concise and professional.\n\n"
            "Format the output strictly as JSON with the following schema:\n{format_instructions}"
        )

        chain = prompt | self.llm | parser
        
        try:
            result = await chain.ainvoke({
                "resume": self.resume_text,
                "question_text": question_text,
                "field_type": field_type,
                "choices_str": choices_str,
                "format_instructions": parser.get_format_instructions()
            })
            return result.get("answer", "")
        except Exception as e:
            print(f"[FormFillerAgent] Error answering question '{question_text}': {e}")
            return "Refer to Resume"

form_filler_agent = FormFillerAgent()
