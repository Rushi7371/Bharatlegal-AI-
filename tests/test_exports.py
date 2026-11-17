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
