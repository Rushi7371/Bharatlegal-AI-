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

SYSTEM = """You are BharatLegal AI, an India-focused legal research and drafting assistant. Be careful, precise, and transparent. You are not a lawyer and do not create an advocate-client relationship. Never invent statutes, sections, case names, citations, quotations, procedural deadlines, or court rules. If you cannot verify a proposition from supplied materials, say so and identify what needs verification against an authoritative source. Distinguish general legal information from legal advice. Ask for missing material facts when essential. For every criminal-law question, establish the date of the alleged conduct and jurisdiction. For conduct on or after 1 July 2024, use the Bharatiya Nyaya Sanhita, 2023 (BNS), where applicable; for earlier conduct, explain that the Indian Penal Code, 1860 (IPC) may continue to apply under repeal-and-savings provisions. The date of complaint or trial does not itself determine the applicable substantive offence. When explaining old IPC provisions, identify corresponding BNS provisions only after checking the statutory text; do not imply an automatic one-to-one equivalence and flag differences in ingredients, subsections and punishment. Critical mapping: IPC section 420 (cheating and dishonestly inducing delivery of property) is generally addressed by BNS section 318(4), if all its ingredients are met; do NOT cite BNS section 319 as the equivalent of IPC 420. BNS section 319 concerns cheating by personation, a distinct offence. IPC section 406 (punishment for criminal breach of trust) is generally addressed by BNS section 316, with the precise subsection depending on the conduct and punishment provision. Never write “BNS sections 318 and 319” as the mapping for IPC 420. State that the precise ingredients, subsection and punishment must be checked against the official statutory text. For legal drafts, use placeholders for unknown facts and add a verification checklist. Flag jurisdiction, limitation, procedural, and factual uncertainties. Do not claim that a draft is ready for filing without review by a qualified Indian advocate."""

def ask_model(prompt: str):
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    errors = []
    if not gemini_key and not openai_key:
        raise HTTPException(status_code=503, detail="Configure GEMINI_API_KEY or OPENAI_API_KEY in Railway.")

    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemini_key, http_options={"timeout": 30000})
            configured = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            # Use currently supported stable fallback models. Gemini 2.5 Flash-Lite
            # was retired on 20 Oct 2026 and must not be used as a fallback.
            models = list(dict.fromkeys([configured, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]))
            for model in models:
                for attempt in range(2):
                    try:
                        response = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(system_instruction=SYSTEM))
                        if response.text and response.text.strip():
                            return response.text
                        errors.append(f"Gemini {model}: empty response")
                        break
                    except Exception as exc:
                        msg = str(exc)[:180]
                        errors.append(f"Gemini {model}: {type(exc).__name__}: {msg}")
                        temporary = "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg.lower()
                        if attempt == 0 and temporary:
                            import time
                            time.sleep(1.2)
                            continue
                        break
        except Exception as exc:
            errors.append(f"Gemini setup: {type(exc).__name__}: {str(exc)[:180]}")

    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, base_url=os.getenv("OPENAI_BASE_URL") or None, timeout=60.0, max_retries=2)
            response = client.responses.create(model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), instructions=SYSTEM, input=prompt)
            if response.output_text and response.output_text.strip():
                return response.output_text
            errors.append("OpenAI returned an empty response.")
        except Exception as exc:
            errors.append(f"OpenAI: {type(exc).__name__}: {str(exc)[:220]}")

    raise HTTPException(status_code=502, detail="AI services are temporarily unavailable. " + " || ".join(errors) + " Please retry shortly. If this continues, check the provider API key, quota and model access in Railway.")


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
    prompt = f"Practice area: {body.practice_area}\\nUser question: {body.message}\\n\\nFor criminal-law issues, explain that the date of alleged conduct determines whether IPC or BNS applies; ask for the date if missing. For conduct on/after 1 July 2024, analyze under BNS where applicable; for earlier conduct, consider IPC and repeal-and-savings rules. When comparing IPC 420 and IPC 406, distinguish cheating from criminal breach of trust. Map IPC 420 to BNS section 318(4) only where its ingredients are met; explicitly state that BNS section 319 is cheating by personation and is NOT the equivalent of IPC 420. Map IPC 406 to the applicable provision in BNS section 316, identifying the precise subsection only after checking the conduct and statutory text. Never list BNS 318 and 319 together as IPC 420 equivalents. Answer with: (1) concise response, (2) comparison table if useful, (3) applicable-date caveat and legal framework, (4) practical next steps, (5) uncertainties and official sources to consult. Do not fabricate citations."
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
