from pathlib import Path
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE = Path(__file__).resolve().parent.parent
CATALOGUE = BASE / "data" / "maharashtra_sources.json"

def load_catalogue():
    with CATALOGUE.open("r", encoding="utf-8") as f:
        return json.load(f)

def check_source(source):
    """Lightweight reachability check only; does not crawl, download or index content."""
    req = Request(source["url"], headers={"User-Agent": "BharatLegalAI-SourceHealthCheck/0.3 (contact: admin)"}, method="HEAD")
    try:
        with urlopen(req, timeout=8) as response:
            return {"id": source["id"], "reachable": True, "http_status": response.status, "checked_url": source["url"], "note": "Reachability only; content is not ingested."}
    except HTTPError as exc:
        return {"id": source["id"], "reachable": exc.code < 500, "http_status": exc.code, "checked_url": source["url"], "note": "Some portals block HEAD requests; this is not proof that the source is unavailable."}
    except (URLError, TimeoutError, OSError) as exc:
        return {"id": source["id"], "reachable": False, "http_status": None, "checked_url": source["url"], "note": str(exc)[:160]}
