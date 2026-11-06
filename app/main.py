from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import os, io
from app.maharashtra_sources import load_catalogue, check_source

BASE = Path(__file__).resolve().parent.parent
app = FastAPI(title="BharatLegal AI", version="0.3.0", description="India-focused legal workflow assistant")

class ChatRequest(BaseModel):
    message: str
    practice_area: str = "General"

class DraftRequest(BaseModel):
    document_type: str
    facts: str
    jurisdiction: str = "India"
    tone: str = "Formal"

SYSTEM = """You are BharatLegal AI, an India-focused legal research and drafting assistant. Be careful, precise, and transparent. You are not a lawyer and do not create an advocate-client relationship. Never invent statutes, sections, case names, citations, quotations, procedural deadlines, or court rules. If you cannot verify a proposition from supplied materials, say so and identify what needs verification against an authoritative source. Distinguish general legal information from legal advice. Ask for missing material facts when essential. For legal drafts, use placeholders for unknown facts and add a verification checklist. Flag jurisdiction, limitation, procedural, and factual uncertainties. Do not claim that a draft is ready for filing without review by a qualified Indian advocate."""

def ask_model(prompt: str):
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    errors = []

    if not gemini_key and not openai_key:
        raise HTTPException(
            status_code=503,
            detail="Configure GEMINI_API_KEY or OPENAI_API_KEY in Railway."
        )

    if gemini_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM
                ),
            )
            if response.text and response.text.strip():
                return response.text

            errors.append("Gemini returned an empty response.")

        except Exception as exc:
            errors.append(f"Gemini: {type(exc).__name__}: {str(exc)[:150]}")

    if openai_key:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=openai_key,
                base_url=os.getenv("OPENAI_BASE_URL") or None,
                timeout=45.0,
                max_retries=1
            )
            response = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                instructions=SYSTEM,
                input=prompt
            )

            if response.output_text and response.output_text.strip():
                return response.output_text

            errors.append("OpenAI returned an empty response.")

        except Exception as exc:
            errors.append(f"OpenAI: {type(exc).__name__}: {str(exc)[:150]}")

    raise HTTPException(
        status_code=502,
        detail="All configured AI providers failed. " + " | ".join(errors)
    )
       
@app.get("/api/health")
def health():
    return {
    "status": "ok",
    "product": "BharatLegal AI",
    "ai_connected": bool(os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    "provider": "Gemini" if os.getenv("GEMINI_API_KEY") else ("OpenAI" if os.getenv("OPENAI_API_KEY") else None),
    "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash") if os.getenv("GEMINI_API_KEY") else (os.getenv("OPENAI_MODEL", "gpt-4.1-mini") if os.getenv("OPENAI_API_KEY") else None)
}
@app.get("/api/maharashtra/sources")
def maharashtra_sources():
    """Return the curated Maharashtra legal/public-source catalogue. Not an indexed corpus."""
    return load_catalogue()

@app.get("/api/maharashtra/sources/health")
def maharashtra_sources_health():
    """Check source website reachability only; does not crawl or ingest data."""
    catalogue = load_catalogue()
    return {"jurisdiction": catalogue["jurisdiction"], "checks": [check_source(source) for source in catalogue["sources"]]}

@app.post("/api/chat")
def chat(body: ChatRequest):
    if not body.message.strip():
        raise HTTPException(400, "Enter a question.")
    prompt = f"Practice area: {body.practice_area}\nUser question: {body.message}\n\nAnswer with: (1) concise response, (2) legal framework to verify, (3) practical next steps, (4) uncertainties and sources to consult. Do not fabricate citations."
    return {"answer": ask_model(prompt)}

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), question: str = Form("Summarise this document, identify issues, obligations, dates, risks and missing information.")):
    name = (file.filename or "document").lower()
    data = await file.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "Maximum upload size is 12 MB.")
    text = ""
    try:
        if name.endswith(".txt") or name.endswith(".md"):
            text = data.decode("utf-8", errors="replace")
        elif name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif name.endswith(".docx"):
            from docx import Document
            doc = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            raise HTTPException(415, "Supported formats: PDF, DOCX, TXT and MD.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Could not read document: {str(exc)[:180]}")
    if not text.strip():
        raise HTTPException(422, "No readable text found. Scanned PDFs require OCR, which is not included in this MVP.")
    text = text[:60000]
    prompt = f"User's requested analysis: {question}\n\nDOCUMENT TEXT (may be incomplete):\n{text}\n\nProvide a structured analysis. Quote short snippets only when useful. Separate facts stated in the document from your interpretation. Identify missing clauses/facts and do not invent legal authorities."
    return {"filename": file.filename, "characters_reviewed": len(text), "analysis": ask_model(prompt)}

@app.post("/api/draft")
def draft(body: DraftRequest):
    if not body.facts.strip():
        raise HTTPException(400, "Provide the facts and drafting instructions.")
    prompt = f"Draft type: {body.document_type}\nJurisdiction: {body.jurisdiction}\nTone: {body.tone}\nFacts and instructions: {body.facts}\n\nPrepare a working draft with clear headings and bracketed placeholders for missing details. Do not invent facts, statutory citations or case law. Finish with a verification checklist for an Indian advocate."
    return {"draft": ask_model(prompt)}

app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
