"""Focused regression tests for BharatLegal AI API helpers.

Run with: python -m unittest discover -s tests -v
These tests do not call external AI providers or upload real client documents.
"""
import asyncio
import unittest
from fastapi import HTTPException

from app.main import (
    DraftExportRequest,
    ScanExportRequest,
    export_draft_docx,
    export_draft_pdf,
    export_scan_docx,
    export_scan_pdf,
    health,
    scan_translation_status,
    _detect_source_language,
    _process_scan_translation,
    DraftRequest,
    draft,
    draft_requirements,
)


async def response_bytes(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    return b"".join(chunks)


class HealthTests(unittest.TestCase):
    def test_health_reports_ok(self):
        self.assertEqual(health()["status"], "ok")

    def test_missing_scan_job_is_clear_404(self):
        with self.assertRaises(HTTPException) as caught:
            scan_translation_status("missing-regression-test-job")
        self.assertEqual(caught.exception.status_code, 404)


class TranslationLanguageDetectionTests(unittest.TestCase):
    def test_english_is_detected_from_latin_script(self):
        self.assertEqual(_detect_source_language("This is a legal document written in English."), "English")

    def test_clear_marathi_text_is_detected_without_provider_call(self):
        sample = "माझ्या मते हा कायदा महाराष्ट्रातील नागरिकांसाठी आहे आणि आम्ही न्यायालयात अर्ज केला आहे."
        self.assertEqual(_detect_source_language(sample), "Marathi")

    def test_clear_hindi_text_is_detected_without_provider_call(self):
        sample = "यह कानून नागरिकों के लिए है और न्यायालय में आवेदन करने के लिए यह जरूरी है।"
        self.assertEqual(_detect_source_language(sample), "Hindi")

    def test_ambiguous_devanagari_uses_provider_classification(self):
        from unittest.mock import patch
        with patch("app.main.ask_model", return_value="Marathi"):
            self.assertEqual(_detect_source_language("प्रकरण क्रमांक १२३, अर्जदार आणि प्रतिवादी"), "Marathi")

    def test_invalid_provider_classification_fails_safely(self):
        from unittest.mock import patch
        with patch("app.main.ask_model", return_value="possibly Hindi"):
            with self.assertRaises(HTTPException) as caught:
                _detect_source_language("प्रकरण क्रमांक १२३, अर्जदार आणि प्रतिवादी")
            self.assertEqual(caught.exception.status_code, 503)



class DraftWorkflowTests(unittest.TestCase):
    def test_initial_draft_uses_placeholders_and_verification_safeguards(self):
        from unittest.mock import patch
        generated = "WORKING DRAFT\\n[Applicant name]\\n[Date]\\nVerification checklist"
        with patch("app.main.ask_model", return_value=generated) as model:
            result = draft(DraftRequest(
                document_type="Petition / pleading outline",
                facts="A tenant seeks relief after a disputed eviction notice.",
                jurisdiction="Maharashtra",
                language="English"
            ))
        self.assertEqual(result["draft"], generated)
        prompt = model.call_args.args[0]
        self.assertIn("bracketed placeholders for missing details", prompt)
        self.assertIn("Do not invent facts, statutory citations, case law", prompt)
        self.assertIn("advocate review warning", prompt)
        self.assertIn("Maharashtra", prompt)


    def test_structured_particulars_are_passed_separately_and_preserved(self):
        from unittest.mock import patch
        generated = "Updated draft with party names and dates."
        particulars = {
            "applicant": "Asha Patil",
            "respondent": "Example Housing Pvt Ltd",
            "event_date": "2026-03-14",
            "district": "Pune",
            "untrusted_extra_field": "Ignore this value"
        }
        with patch("app.main.ask_model", return_value=generated) as model:
            result = draft(DraftRequest(
                document_type="Legal notice",
                facts="The applicant disputes a service charge.",
                jurisdiction="Maharashtra",
                language="English",
                particulars=particulars
            ))
        self.assertEqual(result["draft"], generated)
        prompt = model.call_args.args[0]
        self.assertIn("Applicant: Asha Patil", prompt)
        self.assertIn("Respondent: Example Housing Pvt Ltd", prompt)
        self.assertIn("Event Date: 2026-03-14", prompt)
        self.assertNotIn("Ignore this value", prompt)
        self.assertIn("Do not silently resolve contradictions", prompt)

    def test_structured_particular_over_limit_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            draft(DraftRequest(
                document_type="Legal notice",
                facts="Facts supplied.",
                jurisdiction="Maharashtra",
                particulars={"applicant": "A" * 3001}
            ))
        self.assertEqual(caught.exception.status_code, 413)

    def test_draft_rejects_unsupported_output_language(self):
        with self.assertRaises(HTTPException) as caught:
            draft(DraftRequest(
                document_type="Legal notice",
                facts="Facts supplied.",
                jurisdiction="Maharashtra",
                language="French"
            ))
        self.assertEqual(caught.exception.status_code, 400)

    def test_draft_requires_facts(self):
        with self.assertRaises(HTTPException) as caught:
            draft(DraftRequest(
                document_type="Legal notice", facts="   ",
                jurisdiction="Maharashtra"
            ))
        self.assertEqual(caught.exception.status_code, 400)

    def test_draft_requirements_returns_jurisdiction_specific_caveat(self):
        result = draft_requirements(
            document_type="Petition / pleading outline",
            jurisdiction="Maharashtra"
        )
        self.assertEqual(result["jurisdiction"], "Maharashtra")
        self.assertTrue(any("limitation chronology" in item.lower() for item in result["document_specific"]))
        self.assertIn("not a prescribed form", result["jurisdiction_note"])
        self.assertTrue(result["verification"])

    def test_draft_requirements_rejects_empty_jurisdiction(self):
        with self.assertRaises(HTTPException) as caught:
            draft_requirements(document_type="Legal notice", jurisdiction=" ")
        self.assertEqual(caught.exception.status_code, 400)

class ExportTests(unittest.TestCase):
    def test_scanner_docx_export_is_valid_zip(self):
        response = export_scan_docx(ScanExportRequest(text="Test translation\nSecond line"))
        body = asyncio.run(response_bytes(response))
        self.assertEqual(response.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertTrue(body.startswith(b"PK"))

    def test_scanner_pdf_export_is_valid_pdf(self):
        response = export_scan_pdf(ScanExportRequest(text="Test translation\nSecond line"))
        body = asyncio.run(response_bytes(response))
        self.assertEqual(response.media_type, "application/pdf")
        self.assertTrue(body.startswith(b"%PDF-"))

    def test_draft_docx_export_is_valid_zip(self):
        response = export_draft_docx(DraftExportRequest(text="Working legal draft"))
        body = asyncio.run(response_bytes(response))
        self.assertTrue(body.startswith(b"PK"))

    def test_draft_pdf_export_is_valid_pdf(self):
        response = export_draft_pdf(DraftExportRequest(text="Working legal draft"))
        body = asyncio.run(response_bytes(response))
        self.assertTrue(body.startswith(b"%PDF-"))

    def test_empty_exports_are_rejected(self):
        cases = (
            (export_scan_docx, ScanExportRequest(text="")),
            (export_scan_pdf, ScanExportRequest(text="")),
            (export_draft_docx, DraftExportRequest(text="")),
            (export_draft_pdf, DraftExportRequest(text="")),
        )
        for handler, request in cases:
            with self.subTest(handler=handler.__name__):
                with self.assertRaises(HTTPException) as caught:
                    handler(request)
                self.assertEqual(caught.exception.status_code, 422)


class TranslationPipelineTests(unittest.TestCase):
    def test_auto_detected_marathi_is_used_in_translation_prompt(self):
        from unittest.mock import patch
        source = "माझ्या मते हा कायदा महाराष्ट्रातील नागरिकांसाठी आहे आणि आम्ही न्यायालयात अर्ज केला आहे. हा अर्ज आज सादर केला आहे."
        output = "The law protects citizens and allows them to submit an application before the appropriate court."
        with patch("app.main.ask_model", return_value=output) as model:
            result = _process_scan_translation(
                source.encode("utf-8"), "sample.txt", "text/plain",
                "Auto-detect", "English"
            )
        self.assertEqual(result["source_language"], "Marathi")
        self.assertEqual(result["translated_text"], output)
        self.assertIn("from Marathi into English", model.call_args.args[0])

    def test_same_source_and_target_language_is_rejected(self):
        from unittest.mock import patch
        source = "माझ्या मते हा कायदा महाराष्ट्रातील नागरिकांसाठी आहे आणि आम्ही न्यायालयात अर्ज केला आहे. हा अर्ज आज सादर केला आहे."
        with patch("app.main.ask_model") as model:
            with self.assertRaises(HTTPException) as caught:
                _process_scan_translation(
                    source.encode("utf-8"), "sample.txt", "text/plain",
                    "Auto-detect", "Marathi"
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("already in Marathi", str(caught.exception.detail))
        model.assert_not_called()

    def test_explicit_source_language_skips_auto_classification(self):
        from unittest.mock import patch
        source = "This is a sufficiently long English legal document with facts and dates for translation."
        output = "हा एक पुरेसा मोठा कायदेशीर दस्तऐवज आहे ज्यामध्ये तथ्ये आणि तारखा दिलेल्या आहेत."
        with patch("app.main.ask_model", return_value=output) as model, patch(
            "app.main._detect_source_language", side_effect=AssertionError("must not auto-detect")
        ):
            result = _process_scan_translation(
                source.encode("utf-8"), "sample.txt", "text/plain",
                "English", "Marathi"
            )
        self.assertEqual(result["source_language"], "English")
        self.assertIn("from English into Marathi", model.call_args.args[0])

    def test_image_ocr_text_is_passed_to_translation_provider(self):
        from unittest.mock import patch
        from io import BytesIO
        from PIL import Image
        source = (
            "The applicant submits this written statement before the court. "
            "The incident occurred on the stated date and supporting records are attached."
        )
        output = (
            "अर्जदार न्यायालयासमोर हे लेखी निवेदन सादर करीत आहे. "
            "घटना नमूद केलेल्या दिवशी घडली असून संबंधित कागदपत्रे जोडली आहेत."
        )
        image_buffer = BytesIO()
        Image.new("RGB", (1200, 500), "white").save(image_buffer, format="PNG")
        with patch("pytesseract.image_to_string", return_value=source), patch(
            "app.main.ask_model", return_value=output
        ) as model:
            result = _process_scan_translation(
                image_buffer.getvalue(), "sample.png", "image/png",
                "English", "Marathi"
            )
        self.assertEqual(result["extracted_text"], source)
        self.assertEqual(result["translated_text"], output)
        self.assertIn("from English into Marathi", model.call_args.args[0])

    def test_provider_unavailable_is_returned_as_clear_503(self):
        from unittest.mock import patch
        source = "This is a sufficiently long English legal document with facts and dates for translation."
        with patch("app.main.ask_model", side_effect=HTTPException(503, "provider unavailable")):
            with self.assertRaises(HTTPException) as caught:
                _process_scan_translation(
                    source.encode("utf-8"), "sample.txt", "text/plain",
                    "English", "Marathi"
                )
        self.assertEqual(caught.exception.status_code, 503)

    def test_empty_or_too_short_extracted_text_is_rejected(self):
        from unittest.mock import patch
        with patch("app.main.ask_model") as model:
            with self.assertRaises(HTTPException) as caught:
                _process_scan_translation(
                    b"tiny", "sample.txt", "text/plain",
                    "English", "Marathi"
                )
        self.assertEqual(caught.exception.status_code, 422)
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
