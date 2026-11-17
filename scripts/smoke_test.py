#!/usr/bin/env python3
"""Read-only production smoke test for BharatLegal AI public routes.

Usage:
  python scripts/smoke_test.py
  BHARATLEGAL_BASE_URL=https://example.up.railway.app python scripts/smoke_test.py

This checks route availability and a few page/API invariants only. It does not
upload documents, call paid AI APIs, or certify legal-answer quality.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get(
    "BHARATLEGAL_BASE_URL",
    "https://bharatlegal-ai-web-production.up.railway.app",
).rstrip("/")

CHECKS = {
    "/": (200, b"BharatLegal AI"),
    "/classic": (200, b"Legal work"),
    "/scanner": (200, b"Scan & Translate"),
    "/news": (200, b"News & Legal Updates"),
    "/exams": (200, b"Exam Academy"),
    "/api/health": (200, b'"status":"ok"'),
}


def fetch(path: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "BharatLegalAI-SmokeTest/1.0", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    failures = 0
    print(f"BharatLegal AI smoke test: {BASE}")
    for path, (expected_status, marker) in CHECKS.items():
        try:
            status, body = fetch(path)
            status_ok = status == expected_status
            marker_ok = marker.lower() in body.lower()
            ok = status_ok and marker_ok
            print(
                f"[{'PASS' if ok else 'FAIL'}] {path} "
                f"status={status} marker={'yes' if marker_ok else 'no'}"
            )
            if not ok:
                failures += 1
                if path == "/api/health":
                    try:
                        print("       health payload:", json.loads(body.decode("utf-8")))
                    except Exception:
                        print("       response:", body[:300].decode("utf-8", "replace"))
        except Exception as exc:  # network / TLS / timeout
            failures += 1
            print(f"[FAIL] {path} request error: {exc}")
    print(f"\nResult: {len(CHECKS) - failures}/{len(CHECKS)} checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
