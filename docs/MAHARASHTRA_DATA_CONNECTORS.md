# Maharashtra legal data connectors — MVP 0.3

## What this release actually does
- Adds a curated catalogue of Maharashtra legal and public-information sources in `data/maharashtra_sources.json`.
- Adds `GET /api/maharashtra/sources` to expose the catalogue.
- Adds `GET /api/maharashtra/sources/health` for a lightweight HTTP reachability check.

## What it does NOT do
- It does not download, mirror, scrape, or index every Maharashtra law, judgment, GR, order, or government record.
- It does not provide a complete or current Maharashtra legal database.
- A reachable website is not proof that its content is available for bulk reuse.
- It does not access login-only, fee-based, personal, sealed, confidential, or restricted records.

## Source groups in the catalogue
1. Maharashtra Law and Judiciary Department: Acts, rules, circulars, GRs and publications.
2. Maharashtra Acts and Regulations in Force index and Act List.
3. Bombay High Court legislation list and court portal.
4. Maharashtra Government Resolutions portal.
5. Maharashtra Gazette resources.
6. eCourts public services for permitted public orders.
7. India Code for central legislation applicable in Maharashtra.
8. MahaBhumi and Land Records Acts/Rules pages. Land-record data itself is not mirrored; the portal warns against unauthorised duplication or sale.
9. MahaRERA, IGR Maharashtra, Maharashtra Police, Government of Maharashtra and State Gazetteers.

## Run
```bash
uvicorn app.main:app --reload
```
Then open:
- `http://127.0.0.1:8000/api/maharashtra/sources`
- `http://127.0.0.1:8000/api/maharashtra/sources/health`

## Next engineering phase
Build a permission-aware ingestion pipeline with per-source adapters, document hashes, source URLs, publication/effective dates, version history, OCR, language detection (English/Marathi), deduplication, amendment relationships, provenance, and a review queue. Only ingest documents after confirming the source's terms, access conditions, and legal permissions. Keep a record of the source text and date; never treat a portal link or search snippet as a verified legal proposition.
