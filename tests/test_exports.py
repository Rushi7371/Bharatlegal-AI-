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


if __name__ == "__main__":
    unittest.main()
