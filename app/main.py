from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import os, io, json, base64, tempfile, threading, urllib.request, urllib.error
from app.maharashtra_sources import load_catalogue, check_source
from app.newsroom import router as newsroom_router

BASE = Path(__file__).resolve().parent.parent
_TRANSLATION_MODELS = {}
_TRANSLATION_MODEL_LOCK = threading.Lock()
app = FastAPI(title="BharatLegal AI", version="0.3.0", description="India-focused legal workflow assistant")

class ChatRequest(BaseModel):
    message: str
    practice_area: str = "General"
    language: str = "English"

class DraftRequest(BaseModel):
    document_type: str
    facts: str
    jurisdiction: str = "India"
    tone: str = "Formal"
    language: str = "English"
    particulars: dict[str, str] = {}

class ExamTranslateRequest(BaseModel):
    language: str
    payload: dict

class ExamLessonRequest(BaseModel):
    exam: str
    subject: str
    language: str = "English"

class ExamQuestionsRequest(BaseModel):
    exam: str
    language: str = "English"
    batch: int = 1

class ScanExportRequest(BaseModel):
    text: str
    filename: str = "BharatLegal-translation"

class DraftExportRequest(BaseModel):
    text: str
    filename: str = "BharatLegal-draft"
    language: str = "English"

SYSTEM = """You are BharatLegal AI, an India-focused legal research and drafting assistant. Be careful, precise, and transparent. You are not a lawyer and do not create an advocate-client relationship. Never invent statutes, sections, case names, citations, quotations, procedural deadlines, or court rules. If you cannot verify a proposition from supplied materials, say so and identify what needs verification against an authoritative source. Distinguish general legal information from legal advice. Ask for missing material facts when essential. For every criminal-law question, establish the date of the alleged conduct and jurisdiction. For conduct on or after 1 July 2024, use the Bharatiya Nyaya Sanhita, 2023 (BNS), where applicable; for earlier conduct, explain that the Indian Penal Code, 1860 (IPC) may continue to apply under repeal-and-savings provisions. The date of complaint or trial does not itself determine the applicable substantive offence. When explaining old IPC provisions, identify corresponding BNS provisions only after checking the statutory text; do not imply an automatic one-to-one equivalence and flag differences in ingredients, subsections and punishment. Critical mapping: IPC section 420 (cheating and dishonestly inducing delivery of property) is generally addressed by BNS section 318(4), if all its ingredients are met; do NOT cite BNS section 319 as the equivalent of IPC 420. BNS section 319 concerns cheating by personation, a distinct offence. IPC section 406 (punishment for criminal breach of trust) is generally addressed by BNS section 316, with the precise subsection depending on the conduct and punishment provision. Never write “BNS sections 318 and 319” as the mapping for IPC 420. State that the precise ingredients, subsection and punishment must be checked against the official statutory text. For legal drafts, use placeholders for unknown facts and add a verification checklist. Flag jurisdiction, limitation, procedural, and factual uncertainties. Do not claim that a draft is ready for filing without review by a qualified Indian advocate."""

def ask_model(prompt: str, language: str = "English"):
    allowed = {"English": "English", "Hindi": "Hindi (हिन्दी)", "Marathi": "Marathi (मराठी)"}
    selected = allowed.get(language, "English")
    prompt += f"\n\nRESPONSE LANGUAGE: Write the complete user-facing response in {selected}. Keep statute titles, section numbers, case names and official citations in their authoritative original form; explain them in the selected language. Do not alter legal citations. Draft documents in the selected language. If a translation is uncertain, retain the original legal wording and explain the uncertainty."
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



_SCAN_JOBS = {}
_SCAN_JOBS_LOCK = threading.Lock()

def _run_scan_job(job_id: str, data: bytes, filename: str, mime: str, source_language: str, target_language: str):
    def report_progress(message: str):
        with _SCAN_JOBS_LOCK:
            if job_id in _SCAN_JOBS:
                _SCAN_JOBS[job_id] = {"status": "processing", "message": message}
    try:
        report_progress("Starting document processing…")
        result = _process_scan_translation(data, filename, mime, source_language, target_language, progress_callback=report_progress)
        extracted = str(result.get("extracted_text") or "").strip()
        translated = str(result.get("translated_text") or "").strip()
        # Never label OCR text as a translation, and never report success with blank output.
        if not extracted:
            raise HTTPException(422, "No readable text was extracted. Try a clearer image or select the document's source language.")
        if not translated:
            raise HTTPException(503, "OCR completed, but the translation model returned no translated text. Please retry; no false translation has been displayed.")
        print(f"SCAN_JOB {job_id} complete: extracted_chars={len(extracted)} translated_chars={len(translated)}", flush=True)
        with _SCAN_JOBS_LOCK:
            _SCAN_JOBS[job_id] = {"status": "complete", "message": "Translation complete.", "result": result}
    except HTTPException as exc:
        with _SCAN_JOBS_LOCK:
            _SCAN_JOBS[job_id] = {"status": "error", "message": str(exc.detail)}
    except Exception as exc:
        with _SCAN_JOBS_LOCK:
            _SCAN_JOBS[job_id] = {"status": "error", "message": f"Document processing failed: {type(exc).__name__}: {str(exc)[:180]}"}
    finally:
        # Expire completed jobs without holding a worker thread asleep for 30 minutes.
        def expire_job():
            with _SCAN_JOBS_LOCK:
                _SCAN_JOBS.pop(job_id, None)
        timer = threading.Timer(1800, expire_job)
        timer.daemon = True
        timer.start()


@app.post("/api/scan/export/docx")
def export_scan_docx(request: ScanExportRequest):
    """Create an editable Word document from the user's edited scanner text."""
    text_value = (request.text or "").strip()
    if not text_value:
        raise HTTPException(422, "There is no text to export.")
    if len(text_value) > 500000:
        raise HTTPException(413, "The document is too large to export in one file.")
    from docx import Document
    from docx.shared import Inches, Pt
    from fastapi.responses import StreamingResponse
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    style = document.styles["Normal"]
    style.font.name = "Noto Sans Devanagari"
    style.font.size = Pt(11)
    for line in text_value.splitlines():
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(5)
        run = paragraph.add_run(line)
        run.font.name = "Noto Sans Devanagari"
        run.font.size = Pt(11)
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    safe_name = "".join(ch for ch in request.filename if ch.isalnum() or ch in "-_ ")[:70].strip() or "BharatLegal-translation"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.docx"'}
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers=headers)


@app.post("/api/scan/export/pdf")
def export_scan_pdf(request: ScanExportRequest):
    """Export the user's edited scanner/translation text as a downloadable PDF."""
    text_value = (request.text or "").strip()
    if not text_value:
        raise HTTPException(422, "There is no text to export.")
    if len(text_value) > 200000:
        raise HTTPException(413, "The document is too large to export as PDF in one file.")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib import colors
    from fastapi.responses import StreamingResponse
    import html, re
    contains_devanagari = bool(re.search(r"[\u0900-\u097F]", text_value))
    font_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
    ]
    font_path = next((p for p in font_candidates if os.path.isfile(p)), None)
    if contains_devanagari and not font_path:
        raise HTTPException(503, "Marathi/Hindi PDF export needs the Devanagari font package on the server. Please use Word export for this text until that font is installed.")
    font_name = "BharatLegalDevanagari" if font_path else "Helvetica"
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
        except Exception as exc:
            raise HTTPException(503, "The Devanagari font could not be loaded for PDF export. Please use Word export.") from exc
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("BharatScanBody", parent=styles["BodyText"], fontName=font_name, fontSize=9.5, leading=14, spaceAfter=5, alignment=TA_LEFT, textColor=colors.HexColor("#18212b"))
    story = []
    for line in text_value.splitlines():
        story.append(Paragraph(html.escape(line).replace("  ", "&nbsp;&nbsp;") or " ", body))
        story.append(Spacer(1, 2))
    doc.build(story)
    buffer.seek(0)
    safe_name = "".join(ch for ch in request.filename if ch.isalnum() or ch in "-_ ")[:70].strip() or "BharatLegal-translation"
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'})

@app.post("/api/draft/export/docx")
def export_draft_docx(request: DraftExportRequest):
    """Export the current working draft as an editable Word document."""
    text_value = (request.text or "").strip()
    if not text_value:
        raise HTTPException(422, "There is no draft text to export.")
    if len(text_value) > 500000:
        raise HTTPException(413, "The draft is too large to export in one file.")
    from docx import Document
    from docx.shared import Inches, Pt
    from fastapi.responses import StreamingResponse
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    style = document.styles["Normal"]
    style.font.name = "Noto Sans"
    style.font.size = Pt(11)
    for line in text_value.splitlines():
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(5)
        run = paragraph.add_run(line)
        run.font.name = "Noto Sans"
        run.font.size = Pt(11)
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    safe_name = "".join(ch for ch in request.filename if ch.isalnum() or ch in "-_ ")[:70].strip() or "BharatLegal-draft"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.docx"'}
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers=headers)


@app.post("/api/draft/export/pdf")
def export_draft_pdf(request: DraftExportRequest):
    """Export a working draft to PDF; embed a Unicode font when available."""
    text_value = (request.text or "").strip()
    if not text_value:
        raise HTTPException(422, "There is no draft text to export.")
    if len(text_value) > 200000:
        raise HTTPException(413, "The draft is too large to export as PDF in one file.")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib import colors
    import html, os, re
    contains_devanagari = bool(re.search(r"[\u0900-\u097F]", text_value))
    font_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font_path = next((p for p in font_candidates if os.path.isfile(p)), None)
    if contains_devanagari and (not font_path or "Devanagari" not in font_path):
        raise HTTPException(503, "A Devanagari Unicode font is not installed on this server yet. Word export is available; PDF export for Marathi/Hindi will be enabled after the font is packaged.")
    font_name = "BharatLegalUnicode"
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
        except Exception:
            font_name = "Helvetica"
    else:
        font_name = "Helvetica"
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("BharatBody", parent=styles["BodyText"], fontName=font_name, fontSize=9.5, leading=14, spaceAfter=5, alignment=TA_LEFT, textColor=colors.HexColor("#18212b"))
    story = []
    for line in text_value.splitlines():
        safe_line = html.escape(line).replace("  ", "&nbsp;&nbsp;")
        story.append(Paragraph(safe_line if safe_line else " ", body))
        story.append(Spacer(1, 2))
    doc.build(story)
    buffer.seek(0)
    safe_name = "".join(ch for ch in request.filename if ch.isalnum() or ch in "-_ ")[:70].strip() or "BharatLegal-draft"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'}
    return StreamingResponse(buffer, media_type="application/pdf", headers=headers)


@app.post("/api/scan/translate")
async def start_scan_translation(file: UploadFile = File(...), source_language: str = Form("Auto-detect"), target_language: str = Form("Marathi")):
    """Accept upload quickly and process asynchronously to avoid mobile proxy timeouts."""
    if target_language not in {"English", "Hindi", "Marathi"}:
        raise HTTPException(400, "Choose English, Hindi or Marathi.")
    data = await file.read()
    if not data or len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Choose a file up to 15 MB.")
    import uuid
    job_id = uuid.uuid4().hex
    with _SCAN_JOBS_LOCK:
        _SCAN_JOBS[job_id] = {"status": "queued", "message": "Upload received. Translation is queued."}
    threading.Thread(
        target=_run_scan_job,
        args=(job_id, data, file.filename or "document", file.content_type or "", source_language, target_language),
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "queued"}

@app.get("/api/scan/jobs/{job_id}")
def scan_translation_status(job_id: str):
    with _SCAN_JOBS_LOCK:
        job = _SCAN_JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Translation job expired or was not found. Please upload the document again.")
        response = {key: value for key, value in job.items() if key != "result"}
        if job["status"] == "complete":
            response["result"] = job["result"]
        return response

def _detect_source_language(text: str) -> str:
    """Identify English, Hindi or Marathi text; Devanagari script alone cannot distinguish Hindi from Marathi."""
    import re
    sample = (text or "").strip()[:5000]
    if not sample:
        raise HTTPException(422, "No readable text is available for language detection.")
    devanagari = sum(1 for ch in sample if 0x0900 <= ord(ch) <= 0x097F and ch.isalpha())
    latin = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    if latin > devanagari * 1.25:
        return "English"
    if devanagari == 0:
        return "English"
    # Marathi-exclusive/common lexical and grammatical signals. This is only a
    # fast path for clear cases; mixed or ambiguous Devanagari is classified by AI.
    marathi_markers = re.findall(
        r"\b(?:आहे|आहेत|नाही|नाहीत|म्हणजे|माझ्या|आम्ही|तुम्ही|त्यांनी|यांनी|करण्यासाठी|झाले|झाली|होते|असते|कायदा|न्यायालयात|महाराष्ट्रातील)\b",
        sample
    )
    hindi_markers = re.findall(
        r"\b(?:है|हैं|नहीं|था|थी|थे|करने|लिए|लेकिन|क्योंकि|आपका|हमारा|उसका|इसलिए|न्यायालय)\b",
        sample
    )
    if len(marathi_markers) >= 2 and len(marathi_markers) > len(hindi_markers):
        return "Marathi"
    if len(hindi_markers) >= 2 and len(hindi_markers) > len(marathi_markers):
        return "Hindi"
    # Hindi and Marathi share Devanagari. Never guess Hindi merely because
    # Devanagari is present: ask the configured provider to classify the text.
    prompt = (
        "Identify the primary language of this Indian text. Choose exactly one label: "
        "English, Hindi, or Marathi. Distinguish Hindi from Marathi by vocabulary and "
        "grammar, not by script (both commonly use Devanagari). If mixed, choose the "
        "dominant language. Return only the single label.\n\nTEXT:\n" + sample
    )
    detected = ask_model(prompt, "English").strip().strip(" .").strip("\"'").lower()
    mapping = {"english": "English", "hindi": "Hindi", "marathi": "Marathi"}
    if detected not in mapping:
        raise HTTPException(503, "Could not reliably identify whether the Devanagari document is Hindi or Marathi. Select the source language manually and retry.")
    return mapping[detected]


def _process_scan_translation(data: bytes, filename: str, mime: str, source_language: str, target_language: str, progress_callback=None):
    """Run server-side OCR/text extraction and configured-provider translation in a worker thread."""
    def progress(message):
        if progress_callback:
            progress_callback(message)
    if target_language not in {"English", "Hindi", "Marathi"}:
        raise HTTPException(400, "Choose English, Hindi or Marathi.")
    if source_language not in {"Auto-detect", "English", "Hindi", "Marathi"}:
        raise HTTPException(400, "Choose Auto-detect, English, Hindi or Marathi as the source language.")
    if not data or len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Choose a file up to 15 MB.")
    name = (filename or "document").lower()
    is_image = name.endswith((".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp")) or (mime or "").lower().startswith("image/")
    try:
        progress("Extracting text from the document (OCR)…")
        import pytesseract
        from PIL import Image, ImageOps, ImageEnhance, ImageFilter
        def text_quality(text):
            import re
            words = re.findall(r"[A-Za-z\u0900-\u097F]{2,}", text.lower())
            if not words:
                return -100000
            from collections import Counter
            counts = Counter(words)
            repeated_word_penalty = sum(n - 2 for n in counts.values() if n > 2)
            repeated_phrase_penalty = sum(1 for _ in re.finditer(r"\b(\w+\s+\w+\s+\w+)\b(?:\s+\1\b)+", text.lower()))
            alpha = sum(ch.isalpha() for ch in text)
            visible = sum(not ch.isspace() for ch in text)
            digit_ratio = sum(ch.isdigit() for ch in text) / max(1, visible)
            unique_ratio = len(counts) / max(1, len(words))
            return (alpha * 2 + len(words) * 8 + unique_ratio * 300
                    - repeated_word_penalty * 18 - repeated_phrase_penalty * 120
                    - digit_ratio * 500)
        def clean_ocr_text(text):
            # Remove common phone/browser status-bar artefacts from screenshots
            # before translation; retain meaningful headings and list items.
            import re
            kept = []
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if re.fullmatch(r"(?:\d{1,2}:\d{2}|\d{1,2}%|[|=»{}_—–-]+)", line):
                    continue
                if re.search(r"\b(?:5G|4G|LTE|VoLTE|Wi-?Fi)\b", line, re.I) and len(line.split()) < 8:
                    continue
                if re.search(r"\b(?:reply to chatgpt|copy edited text|copy original ocr|scan & translate|battery)\b", line, re.I):
                    continue
                words = re.findall(r"[A-Za-z\u0900-\u097F]{2,}", line)
                if len(words) == 0:
                    continue
                if len(words) == 1 and len(words[0]) < 4:
                    continue
                kept.append(line)
            return "\n".join(kept).strip()

        def ocr_quality(text):
            # Prefer readable document text, not phone chrome or isolated OCR fragments.
            cleaned = clean_ocr_text(text)
            return text_quality(cleaned) - max(0, len(text) - len(cleaned)) * 2
        def ocr_image(image):
            image = ImageOps.exif_transpose(image).convert("RGB")
            image = ImageOps.grayscale(image)
            image = ImageOps.autocontrast(image)
            if image.width < 1600:
                scale = min(2.0, 1600 / max(1, image.width))
                image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
            image = ImageEnhance.Contrast(image).enhance(1.5)
            image = image.filter(ImageFilter.SHARPEN)
            if source_language == "Hindi":
                languages = ["hin", "hin+eng"]
            elif source_language == "Marathi":
                languages = ["mar", "mar+eng"]
            elif source_language == "English":
                languages = ["eng"]
            else:
                languages = ["eng", "hin+eng", "mar+eng", "eng+hin+mar"]
            candidates = []
            for lang in languages:
                for psm in (6, 11):
                    try:
                        result = pytesseract.image_to_string(image, lang=lang, config=f"--oem 1 --psm {psm}").strip()
                        candidates.append((ocr_quality(result), result))
                    except Exception as exc:
                        print(f"OCR candidate {lang}/psm{psm} failed: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
            best = max(candidates, key=lambda item: item[0])[1] if candidates else ""
            return clean_ocr_text(best)
        if is_image:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            original = ocr_image(image)
        elif name.endswith(".pdf") or mime == "application/pdf":
            import re
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text_pages = [p.extract_text() or "" for p in reader.pages]
            extracted = "\n\n".join(text_pages).strip()
            # PDFs with embedded/custom fonts can return non-empty but corrupted text.
            # Do not trust "non-empty" extraction alone: OCR the pages when the text
            # contains heavy repetition or very low lexical diversity.
            extracted_words = re.findall(r"[A-Za-z\u0900-\u097F]{2,}", extracted.lower())
            extracted_unique = len(set(extracted_words)) / max(1, len(extracted_words))
            repeated_words = len(extracted_words) - len(set(extracted_words))
            needs_ocr = (not extracted or len(extracted_words) < 12
                         or (len(extracted_words) > 30 and extracted_unique < 0.48)
                         or (len(extracted_words) > 30 and repeated_words > len(extracted_words) * 0.42))
            original = extracted
            if needs_ocr:
                progress("PDF text layer looks unreliable. Re-reading pages with OCR…")
                import fitz
                pdf = fitz.open(stream=data, filetype="pdf")
                pages = []
                if len(pdf) > 25:
                    raise HTTPException(413, "Scanned PDF is over 25 pages. Split it into smaller files for faster processing.")
                for page_number, page in enumerate(pdf, start=1):
                    progress(f"OCR reading PDF page {page_number} of {len(pdf)}…")
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                    pages.append(ocr_image(img))
                ocr_text = "\n\n".join(pages).strip()
                if text_quality(ocr_text) > text_quality(extracted):
                    original = ocr_text
        elif name.endswith(".docx") or "wordprocessingml.document" in mime:
            from docx import Document
            doc = Document(io.BytesIO(data))
            chunks = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    chunks.append(" | ".join(cell.text for cell in row.cells))
            original = "\n".join(chunks).strip()
        elif name.endswith((".txt", ".md")) or mime.startswith("text/"):
            original = data.decode("utf-8-sig", errors="replace").strip()
        else:
            raise HTTPException(415, "Supported files: JPG, PNG, WEBP, text-based or scanned PDF, DOCX, TXT and MD.")
        if not original:
            raise HTTPException(422, "No readable text found. Check image quality and selected OCR language.")
        # Fail fast on visibly corrupted extraction instead of feeding garbage into MT.
        import re
        quality_words = re.findall(r"[A-Za-z\u0900-\u097F]{2,}", original.lower())
        if len(quality_words) >= 20:
            from collections import Counter
            word_counts = Counter(quality_words)
            repeated_ratio = sum(max(0, n - 1) for n in word_counts.values()) / len(quality_words)
            unique_ratio = len(word_counts) / len(quality_words)
            suspicious_markers = sum(original.lower().count(x) for x in ("longname", "longdayname", "indian nationality", "xxxxxxxx"))
            if repeated_ratio > 0.48 or (unique_ratio < 0.24 and len(quality_words) > 40) or suspicious_markers >= 3:
                raise HTTPException(
                    422,
                    "Text extraction quality is too low to translate safely. The PDF may have a broken text layer or poor scan. "
                    "Please upload a clearer scan or select the document's actual source language; no translation was generated."
                )
        elif len(original.strip()) < 25:
            raise HTTPException(422, "Too little readable text was extracted. Please upload a clearer scan.")
        original = original[:20000]
        progress(f"Text extraction complete ({len(original):,} characters). Preparing translation model…")
        # Use the configured hosted AI provider for document translation.
        # The previous in-process Transformers/OPUS-MT model downloads and loads
        # hundreds of MB into the web container. Railway logs showed the container
        # being KILLED while scan jobs were running, which erased the in-memory job
        # registry and caused the client to report "Could not check translation status".
        # Keep OCR local, but run translation through the configured API provider.
        if source_language in {"English", "Hindi", "Marathi"}:
            detected_source = source_language
        else:
            detected_source = _detect_source_language(original)
        if detected_source == target_language:
            raise HTTPException(
                400,
                f"The document is already in {detected_source}. Choose a different target language."
            )
        translated_parts = []
        paragraphs = [p.strip() for p in original.splitlines() if p.strip()]
        chunks = []
        current = ""
        for paragraph in paragraphs:
            if len(current) + len(paragraph) + 1 > 2600 and current:
                chunks.append(current)
                current = paragraph
            else:
                current = (current + "\n" + paragraph).strip()
        if current:
            chunks.append(current)
        if not chunks:
            chunks = [original]
        for index, part in enumerate(chunks, 1):
            progress(f"Translating section {index} of {len(chunks)} with the configured AI provider…")
            prompt = (
                f"Translate this document from {detected_source} into {target_language}. "
                "Return only the translation. Do not explain, summarize, add commentary, "
                "or transliterate full sentences. Preserve the meaning, headings, numbering, "
                "bullet points, names, dates and paragraph structure. Use natural, complete "
                "Indian Marathi/Hindi as requested.\n\nDOCUMENT:\n" + part
            )
            translated_part = ask_model(prompt, target_language)
            if not translated_part or not translated_part.strip():
                raise HTTPException(503, f"Translation provider returned an empty result for section {index}.")
            translated_parts.append(translated_part.strip())
        translated = "\n\n".join(translated_parts).strip()
        # Validate provider output. If the first pass looks corrupted, retry with
        # smaller chunks rather than returning numeric/repeated garbage to the user.
        import re
        def translation_quality(value):
            words = re.findall(r"[A-Za-z\u0900-\u097F]{2,}", value)
            visible = [ch for ch in value if not ch.isspace()]
            digit_ratio = sum(ch.isdigit() for ch in visible) / max(1, len(visible))
            unique_ratio = len(set(w.lower() for w in words)) / max(1, len(words))
            letters = sum(1 for ch in value if ch.isalpha())
            devanagari = sum(1 for ch in value if 0x0900 <= ord(ch) <= 0x097F and ch.isalpha())
            script_ratio = devanagari / max(1, letters)
            source_words = re.findall(r"[A-Za-z\u0900-\u097F]{2,}", original)
            output_too_short = len(original) >= 120 and len(words) < max(8, int(len(source_words) * 0.30))
            bad = (len(original) >= 40 and (
                len(words) < 5 or digit_ratio > 0.30
                or (len(words) >= 12 and (1 - unique_ratio) > 0.55)
                or output_too_short
                or (target_language in {"Hindi", "Marathi"} and script_ratio < 0.30)
            ))
            return bad, {"words": len(words), "digit_ratio": round(digit_ratio, 3),
                         "unique_ratio": round(unique_ratio, 3), "devanagari_ratio": round(script_ratio, 3)}
        bad_output, quality = translation_quality(translated)
        if bad_output:
            print(f"Provider translation failed quality checks: {quality}; attempting smaller-chunk provider retry.", flush=True)
            try:
                translated_parts = []
                for offset in range(0, len(original), 3000):
                    part = original[offset:offset + 3000]
                    prompt = (
                        f"Translate the following document text from {detected_source} into {target_language}. "
                        "Return only the translation, with no preface, explanation, markdown fences or commentary. "
                        "Preserve all headings, numbering, bullet points, names, dates and meaning. "
                        "Use natural, complete Indian Marathi/Hindi as requested; do not transliterate English "
                        "sentences or replace them with numbers. Keep the same paragraph structure.\\n\\n"
                        f"DOCUMENT TEXT:\n{part}"
                    )
                    translated_parts.append(ask_model(prompt, target_language))
                candidate = "\n\n".join(x.strip() for x in translated_parts if x and x.strip()).strip()
                fallback_bad, fallback_quality = translation_quality(candidate)
                if candidate and not fallback_bad:
                    translated = candidate
                    print(f"Configured AI translation fallback passed quality checks: {fallback_quality}", flush=True)
                else:
                    print(f"Configured AI translation fallback also failed: {fallback_quality}", flush=True)
                    raise RuntimeError("Fallback translation did not pass output-quality checks.")
            except Exception as fallback_error:
                raise HTTPException(
                    503,
                    "The translation provider produced corrupted output and a smaller-chunk retry could not produce a reliable translation. "
                    f"Details: {type(fallback_error).__name__}: {str(fallback_error)[:180]}. "
                    "Please retry later; the original OCR text remains available only in this scan session."
                )
        progress("Translation finished. Preparing editable results…")
        return {"source_language": detected_source, "target_language": target_language, "extracted_text": original, "translated_text": translated, "filename": filename, "mode": "hosted_ai"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Document processing failed. " + str(exc)[:180])

@app.get("/api/health")
def health():
    return {
    "status": "ok",
    "product": "BharatLegal AI",
    "ai_connected": bool(os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    "provider": "Gemini" if os.getenv("GEMINI_API_KEY") else ("OpenAI" if os.getenv("OPENAI_API_KEY") else None),
    "model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash") if os.getenv("GEMINI_API_KEY") else (os.getenv("OPENAI_MODEL", "gpt-4.1-mini") if os.getenv("OPENAI_API_KEY") else None)
}

def _ecourtsindia_partner_get(path: str):
    """Server-side proxy for optional third-party court-structure data. Never expose partner tokens to browsers."""
    token = os.getenv("ECOURTSINDIA_PARTNER_API_KEY", "").strip()
    if not token:
        raise HTTPException(503, "Court directory integration is not configured. Add ECOURTSINDIA_PARTNER_API_KEY after obtaining an authorized partner account.")
    if not path.startswith("/api/partner/causelist/court-structure/"):
        raise HTTPException(400, "Unsupported court-directory request.")
    base = os.getenv("ECOURTSINDIA_API_BASE_URL", "https://webapi.ecourtsindia.com").rstrip("/")
    request = urllib.request.Request(
        base + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "BharatLegal-AI/0.3"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        # Provider documentation currently returns arrays for court-structure routes.
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            payload = payload["data"]
        if not isinstance(payload, list):
            raise ValueError("Unexpected court directory response shape")
        return {"data": payload, "source": "eCourtsIndia partner API (independent third-party provider)", "official_government_api": False}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(502, "Court directory provider rejected the configured partner token. Check its validity and access permissions.")
        if exc.code == 429:
            raise HTTPException(503, "Court directory provider rate limit reached. Please retry later.")
        raise HTTPException(502, "Court directory provider returned an upstream error.")
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise HTTPException(503, "Court directory provider is temporarily unavailable or returned invalid data.")


@app.get("/api/courts/structure/status")
def court_directory_status():
    return {
        "configured": bool(os.getenv("ECOURTSINDIA_PARTNER_API_KEY")),
        "provider": "eCourtsIndia partner API",
        "provider_type": "independent third-party service, not a Government of India API",
        "official_verification_url": "https://services.ecourts.gov.in/ecourtindia_v6/"
    }


@app.get("/api/courts/structure/states")
def court_directory_states():
    return _ecourtsindia_partner_get("/api/partner/causelist/court-structure/states")


@app.get("/api/courts/structure/districts")
def court_directory_districts(state: str):
    if not state or len(state) > 12 or not state.replace("-", "").isalnum():
        raise HTTPException(400, "A valid provider state code is required.")
    return _ecourtsindia_partner_get("/api/partner/causelist/court-structure/states/" + state + "/districts")


@app.get("/api/courts/structure/complexes")
def court_directory_complexes(state: str, district_code: str):
    if not state or len(state) > 12 or not state.replace("-", "").isalnum():
        raise HTTPException(400, "A valid provider state code is required.")
    if not district_code or len(district_code) > 24 or not district_code.replace("-", "").isalnum():
        raise HTTPException(400, "A valid provider district code is required.")
    path = "/api/partner/causelist/court-structure/states/" + state + "/districts/" + district_code + "/complexes"
    return _ecourtsindia_partner_get(path)


@app.get("/api/courts/structure/courts")
def court_directory_courts(state: str, district_code: str, complex_code: str):
    for value, label, limit in ((state, "state", 12), (district_code, "district", 24), (complex_code, "court-complex", 24)):
        if not value or len(value) > limit or not value.replace("-", "").isalnum():
            raise HTTPException(400, "A valid provider " + label + " code is required.")
    path = "/api/partner/causelist/court-structure/states/" + state + "/districts/" + district_code + "/complexes/" + complex_code + "/courts"
    return _ecourtsindia_partner_get(path)


@app.get("/api/draft/requirements")
def draft_requirements(document_type: str, jurisdiction: str):
    """Return a drafting intake checklist, not a statement of prescribed court forms or local filing rules."""
    if not jurisdiction.strip():
        raise HTTPException(400, "Select a State, Union Territory or central jurisdiction.")
    common = [
        {"id": "parties", "label": "Full legal names and roles of all parties", "required_for_draft": True},
        {"id": "addresses", "label": "Current addresses for correspondence / service", "required_for_draft": True},
        {"id": "facts_dates", "label": "Chronology of material events with dates", "required_for_draft": True},
        {"id": "relief", "label": "Specific relief, response or outcome requested", "required_for_draft": True},
        {"id": "documents", "label": "Supporting documents and annexures (or state none)", "required_for_draft": False},
        {"id": "forum", "label": "Proposed court, tribunal or authority, if known", "required_for_draft": False},
    ]
    specific = {
        "Legal notice": ["Legal basis and relevant contract / transaction", "Breach or grievance and demand", "Time allowed for compliance (verify before use)"],
        "Reply to legal notice": ["Date and complete copy of notice", "Point-by-point response and admissions / denials", "Documents supporting the response"],
        "Affidavit": ["Deponent identity and capacity", "Facts within personal knowledge versus information / belief", "Place and date of affirmation; attestation requirements to verify"],
        "Application / representation": ["Name of receiving authority", "Relevant application / reference number", "Administrative decision or action requested"],
        "Commercial agreement": ["Commercial scope and deliverables", "Consideration, payment and tax terms", "Term, termination, liability and dispute resolution"],
        "Memorandum of understanding": ["Purpose and binding / non-binding intent", "Roles, milestones and contributions", "Duration, exit and dispute resolution"],
        "Petition / pleading outline": ["Cause of action / legal grounds", "Territorial and subject-matter jurisdiction facts", "Limitation chronology and interim relief, if any"],
        "Written submissions outline": ["Case number and forum", "Issues for determination and pleadings relied on", "Authorities and record references (verify citations)"],
    }
    labels = specific.get(document_type, ["Purpose of the document", "Relevant legal / factual background"])
    return {
        "document_type": document_type, "jurisdiction": jurisdiction,
        "checklist": common, "document_specific": labels,
        "jurisdiction_note": f"This intake checklist is for {jurisdiction}; it is not a prescribed form or confirmation of local procedure.",
        "verification": [
            "Confirm the current official statute, state amendments, rules and notifications applicable to the selected jurisdiction.",
            "Confirm the correct court / tribunal / authority, territorial jurisdiction, limitation, court fee, format, stamping, notarisation and service rules from its official source.",
            "Do not assume local forms or deadlines from this checklist. Obtain advocate review before signing, service or filing."
        ],
        "official_portals": [
            {"name": "India Code", "url": "https://www.indiacode.nic.in/"},
            {"name": "eCourts", "url": "https://ecourts.gov.in/"},
            {"name": "e-Gazette of India", "url": "https://egazette.gov.in/"}
        ]
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
    return {"answer": ask_model(prompt, body.language)}

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), question: str = Form("Summarise this document, identify issues, obligations, dates, risks and missing information."), language: str = Form("English")):
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
    return {"filename": file.filename, "characters_reviewed": len(text), "analysis": ask_model(prompt, language)}

@app.post("/api/draft")
def draft(body: DraftRequest):
    facts = (body.facts or "").strip()
    jurisdiction = (body.jurisdiction or "").strip()
    if not facts:
        raise HTTPException(400, "Provide the facts and drafting instructions.")
    if not jurisdiction:
        raise HTTPException(400, "Select the State, Union Territory or India-wide jurisdiction.")
    if len(facts) > 60000:
        raise HTTPException(413, "Drafting instructions are too long. Please keep them under 60,000 characters.")
    if body.language not in {"English", "Hindi", "Marathi"}:
        raise HTTPException(400, "Choose English, Hindi or Marathi.")
    allowed_particulars = {
        "court", "place", "district", "taluka", "police_station", "court_level",
        "local_language", "applicant", "respondent", "applicant_address",
        "respondent_address", "event_date", "document_date", "filing_date",
        "case_number", "advocate", "relief", "annexures", "other"
    }
    clean_particulars = {}
    for key, value in (body.particulars or {}).items():
        if key in allowed_particulars and isinstance(value, str) and value.strip():
            if len(value) > 3000:
                raise HTTPException(413, f"The {key.replace('_', ' ')} particular is too long.")
            clean_particulars[key] = value.strip()
    document_type_guidance = {
        "Legal notice": "DOCUMENT STRUCTURE: identify sender and recipient; state the engagement/transaction and concise chronology; describe the alleged breach or grievance without overstating disputed facts; set out the requested cure or response; specify a response period only if supplied or independently verified; reserve rights without making unsupported threats; list enclosures and service details.",
        "Reply to legal notice": "DOCUMENT STRUCTURE: identify the notice and its date; respond paragraph-by-paragraph where the notice text is available; clearly distinguish admissions, denials, lack of knowledge and requests for proof; state the recipient's factual position and supporting documents; avoid accidental admissions; reserve rights. Do not invent the notice's contents.",
        "Affidavit": "DOCUMENT STRUCTURE: title and forum placeholders; deponent identity and capacity; numbered factual paragraphs; clearly separate personal knowledge from information/belief and identify the source for the latter; verification/solemn affirmation block with place/date placeholders. Mark oath, attestation, exhibit and notarisation requirements for local verification; do not claim an affidavit is validly sworn.",
        "Application / representation": "DOCUMENT STRUCTURE: addressee/authority; applicant identity and reference number; concise background and chronology; specific grounds or grievance; numbered requests/prayer; list enclosures; date/place/signature and acknowledgement placeholders. Do not imply the authority has jurisdiction unless established.",
        "Commercial agreement": "DOCUMENT STRUCTURE: parties and authority; definitions; scope/deliverables; responsibilities and timelines; consideration, invoicing and taxes; representations; confidentiality/data handling; IP ownership/licence; term, termination and consequences; liability/indemnity; force majeure; notices; governing law, jurisdiction and dispute resolution; signatures and schedules. Flag negotiated blanks and clauses needing specialist review; do not assume enforceability.",
        "Memorandum of understanding": "DOCUMENT STRUCTURE: parties and purpose; background; objectives and contributions; governance/contact persons; milestones and dependencies; costs/resources; confidentiality and IP; duration, amendment and exit; dispute handling; signatures. Explicitly identify which clauses are intended to bind and which are non-binding; do not decide that status without the parties' instructions.",
        "Petition / pleading outline": "DOCUMENT STRUCTURE: forum and jurisdiction placeholders; parties and descriptions; material facts in numbered chronological paragraphs; cause of action/legal grounds; limitation chronology; jurisdiction facts; interim relief if requested; final prayer; list of documents/annexures; verification/affidavit placeholders. Do not assert maintainability, limitation or a prescribed format as settled without verified facts and current rules.",
        "Written submissions outline": "DOCUMENT STRUCTURE: court/tribunal and case number; appearances/party represented; brief procedural and factual background; issues for determination; submissions issue-by-issue tied to pleadings and record references; authorities only when supplied or verified; relief/prayer. Mark every missing record page, case citation or proposition for counsel verification."
    }
    drafting_guidance = document_type_guidance.get(
        body.document_type,
        "DOCUMENT STRUCTURE: use a clear title, parties, purpose, material facts, requested outcome, supporting-document list, signature/date placeholders and a missing-information checklist. Tailor the structure to the requested document without inventing prescribed forms."
    )
    particulars_text = "\n".join(
        f"{key.replace('_', ' ').title()}: {value}"
        for key, value in clean_particulars.items()
    ) or "No additional particulars supplied."
    prompt = f"""Draft type: {body.document_type}
Jurisdiction / State / Union Territory: {jurisdiction}
Tone: {body.tone}
Initial facts and drafting instructions: {facts}

ADDITIONAL STRUCTURED PARTICULARS (preserve exactly; do not infer missing values):
{particulars_text}

{drafting_guidance}

Prepare a jurisdiction-aware working draft with clear headings and bracketed placeholders for missing details. Use the selected State/UT, district, taluka/tehsil, police station and court/forum particulars where supplied. Distinguish central law from state-specific legislation, rules and local court practice. Do not assume that a state has a particular prescribed form, fee, limitation period, court hierarchy or filing requirement unless verified from a supplied authoritative source. If the applicable local rule or form is not verified, explicitly mark it for verification and identify the relevant official authority/source to consult. For criminal matters, ask for the date of alleged conduct and apply the appropriate IPC/BNS transition caveat. Do not invent facts, statutory citations, case law, court rules or filing requirements. Preserve supplied names, dates, amounts and addresses exactly; flag inconsistencies and missing mandatory particulars instead of guessing. Do not silently resolve contradictions between initial facts and additional particulars; flag them for confirmation. Finish with (1) missing-information checklist, (2) jurisdiction and procedure verification checklist, and (3) advocate review warning. This is a working draft, not a filing-ready document."""
    return {"draft": ask_model(prompt, body.language)}

@app.post("/api/exams/lesson")
def generate_exam_lesson(body: ExamLessonRequest):
    allowed = {"JMFC / Civil Judge", "AIBE", "Assistant Public Prosecutor", "Government / PSU / Bank Law Officer", "UGC NET Law", "Common Legal Studies Library"}
    if body.exam not in allowed:
        raise HTTPException(400, "Unknown examination")
    language = body.language if body.language in ("English", "Hindi", "Marathi") else "English"
    prompt = f"""You are the in-house law exam educator for BharatLegal AI in India. Prepare a self-contained, useful study lesson for a law student preparing for {body.exam}.
Subject/topic: {body.subject}
Language: {language}.
Write substantive teaching material, NOT a reading list and NOT instructions to go elsewhere. Target 700-1000 words. Use clear headings:
1. What this topic covers
2. Core concepts and definitions
3. Key statutory framework and important provisions (verify accuracy; if uncertain, say so rather than inventing section numbers)
4. How the legal rule works, with 2 practical illustrations
5. Important distinctions / common exam traps
6. Exam-focused revision points
7. Five quick self-check questions with short answers.
Use current Indian law. Where relevant, explain the transition between IPC/BNS, CrPC/BNSS and Evidence Act/BSA, including commencement on 1 July 2024 and savings/transition caveats. Do not claim this is an official paper or reproduce copyrighted coaching material. Do not invent cases, citations, amendments, section numbers, syllabus claims or official notifications. Explain that state judicial-service syllabi differ and the current notification controls. Make the material understandable without clicking external links. Return plain text with headings; no JSON and no markdown table."""
    lesson = ask_model(prompt, language)
    if not lesson or len(lesson.strip()) < 250:
        raise HTTPException(502, "Study lesson could not be generated. Please retry.")
    return {"exam": body.exam, "subject": body.subject, "language": language, "lesson": lesson}
    

@app.post("/api/exams/questions")
def generate_exam_questions(body: ExamQuestionsRequest):
    allowed = {"JMFC / Civil Judge", "AIBE", "Assistant Public Prosecutor", "Government / PSU / Bank Law Officer", "UGC NET Law"}
    if body.exam not in allowed:
        raise HTTPException(400, "Unknown examination")
    language = body.language if body.language in ("English", "Hindi", "Marathi") else "English"
    batch = max(1, min(int(body.batch or 1), 1000000))
    prompt = f"""Create exactly 10 fresh, original, objectively answerable multiple-choice questions for Indian law exam preparation.
Exam: {body.exam}
Language: {language}
Practice batch number: {batch}. Make this batch distinct; do not repeat generic starter questions. Vary topics, statutory interpretation and application scenarios appropriate to this examination.
Use current Indian law. Account for the 1 July 2024 commencement and savings/transition rules for BNS, BNSS and BSA where relevant. Never invent section numbers, cases, legal rules or exam notifications. If a point is uncertain, choose another question.
Return ONLY valid JSON with this exact structure:
{{"questions":[{{"q":"question text","o":["option A","option B","option C","option D"],"a":0,"e":"Concise explanation establishing why the keyed answer is correct."}}]}}
Requirements: exactly 10 question objects; four plausible distinct options each; a is the zero-based index of exactly one correct option (0-3); e explains the correct legal principle and, where useful, its statutory basis. Questions must be self-contained, no duplicate questions within the batch, no "all of the above", no trick ambiguity. Write all question text, options and explanations in {language}; preserve official Act titles and section numbers accurately. These are original practice questions, not purported past-paper questions."""
    raw = ask_model(prompt, language).strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    try:
        data = json.loads(raw)
        qs = data.get("questions", [])
        if len(qs) != 10:
            raise ValueError("Expected 10 questions")
        for q in qs:
            if not isinstance(q.get("q"), str) or len(q.get("o", [])) != 4 or q.get("a") not in (0, 1, 2, 3) or not isinstance(q.get("e"), str):
                raise ValueError("Invalid question structure")
        # Run a dedicated translation pass for non-English batches. This prevents
        # the generator from silently returning English questions when Marathi/Hindi
        # was requested. Preserve option order and answer indexes exactly.
        if language in ("Marathi", "Hindi"):
            translation_prompt = f"""Translate every learner-facing string in this JSON into natural, complete {language} for Indian law students.
Translate every question (q), all four options (o), and explanation (e). Do not leave English sentences untranslated. Preserve statutory names, Act titles, abbreviations, section numbers, dates and legal citations accurately; they may remain in their authoritative form where appropriate. Do not change the meaning, question count, option order, or answer index (a). Return ONLY valid JSON with the same structure and keys:
{json.dumps({"questions": qs}, ensure_ascii=False)}
"""
            translated_raw = ask_model(translation_prompt, language).strip()
            if translated_raw.startswith("```"):
                translated_raw = translated_raw.split("\n", 1)[-1]
                if translated_raw.endswith("```"):
                    translated_raw = translated_raw[:-3].strip()
            translated_data = json.loads(translated_raw)
            translated_qs = translated_data.get("questions", [])
            if len(translated_qs) != 10:
                raise ValueError("Translation returned an incomplete batch")
            for q in translated_qs:
                if not isinstance(q.get("q"), str) or len(q.get("o", [])) != 4 or q.get("a") not in (0, 1, 2, 3) or not isinstance(q.get("e"), str):
                    raise ValueError("Invalid translated question structure")
            qs = translated_qs
        return {"exam": body.exam, "language": language, "batch": batch, "questions": qs}
    except Exception:
        raise HTTPException(502, "Could not prepare a valid 10-question batch. Please retry.")

@app.post("/api/exams/translate")
def translate_exam_content(body: ExamTranslateRequest):
    language = body.language if body.language in ("Hindi", "Marathi") else "English"
    if language == "English":
        return {"translation": body.payload}
    prompt = f"""Translate the complete legal exam learning content below into {language}.
Return ONLY valid JSON with exactly the same keys and structure. Translate all user-facing prose, subject names, questions, answer options, explanations, descriptions and headings. Preserve question ordering, option ordering, statutory names, Act titles, case names, abbreviations (CPC, BNS, BNSS, BSA, AIBE, JMFC), section numbers, dates and legal citations accurately. Keep the legal meaning precise; do not add or remove content. Use clear, natural Indian {language} suitable for law students. Do not translate URLs (none are expected). JSON input:
{json.dumps(body.payload, ensure_ascii=False)}
"""
    raw = ask_model(prompt, language)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        translated = json.loads(cleaned)
    except Exception:
        raise HTTPException(502, "Could not parse the translation. Please retry or use English.")
    return {"translation": translated}

@app.get("/", include_in_schema=False)
def workspace_home():
    # Serve the current workspace directly and prevent stale mobile/browser caches.
    return FileResponse(
        BASE / "static" / "workspace.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )

@app.get("/classic", include_in_schema=False)
def classic_workspace():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/mobile", include_in_schema=False)
def mobile_redirect():
    return RedirectResponse(url="/mobile/", status_code=307)

app.include_router(newsroom_router)

@app.get("/news", include_in_schema=False)
def newsroom_page():
    return FileResponse(BASE / "static" / "news.html")

@app.get("/exams", include_in_schema=False)
def exams_page():
    return FileResponse(BASE / "static" / "exams.html")

@app.get("/scanner", include_in_schema=False)
def scanner_page():
    return FileResponse(BASE / "static" / "scanner.html")

@app.get("/court-services", include_in_schema=False)
def court_services_page():
    return FileResponse(BASE / "static" / "court-services.html", headers={"Cache-Control": "no-store"})

app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
