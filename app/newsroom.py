from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import urllib.request, urllib.parse, xml.etree.ElementTree as ET, os

router = APIRouter()
_cache = {"until": None, "items": []}
STATES = ["Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh","Goa","Gujarat","Haryana","Himachal Pradesh","Jharkhand","Karnataka","Kerala","Madhya Pradesh","Maharashtra","Manipur","Meghalaya","Mizoram","Nagaland","Odisha","Punjab","Rajasthan","Sikkim","Tamil Nadu","Telangana","Tripura","Uttar Pradesh","Uttarakhand","West Bengal","Delhi","Jammu and Kashmir","Ladakh","Puducherry"]

def _feed(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl":"en-IN", "gl":"IN", "ceid":"IN:en"})
    request = urllib.request.Request(url, headers={"User-Agent":"BharatLegalAI/1.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        root = ET.fromstring(response.read(1500000))
    rows = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        source = node.find("source")
        publisher = (source.text or "").strip() if source is not None else "News publisher"
        snippet = (node.findtext("description") or "")[:900]
        try: published = parsedate_to_datetime(node.findtext("pubDate") or "").astimezone(timezone.utc).isoformat()
        except Exception: published = None
        if title and link: rows.append({"title":title,"url":link,"publisher":publisher,"snippet":snippet,"published_at":published})
    return rows

def _items():
    now = datetime.now(timezone.utc)
    if _cache["until"] and _cache["until"] > now: return _cache["items"]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    queries = ["India legal news Supreme Court High Court law","India law ministry legislation notification legal update","Maharashtra Bombay High Court legal news judgment"]
    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(_feed, q) for q in queries]):
            try: rows.extend(future.result())
            except Exception: pass
    unique, result = set(), []
    for item in sorted(rows, key=lambda x:x.get("published_at") or "", reverse=True):
        if item["url"] in unique: continue
        unique.add(item["url"])
        text = (item["title"]+" "+item["snippet"]+" "+item["publisher"]).lower()
        item["state"] = next((s for s in STATES if s.lower() in text), None)
        item["category"] = "Supreme Court" if "supreme court" in text else "High Courts" if "high court" in text else "Legislation" if any(k in text for k in ["bill","act","amendment","gazette","legislation"]) else "Government" if any(k in text for k in ["ministry","government","notification","pib"]) else "Legal News"
        result.append(item)
    _cache.update({"until":now+timedelta(minutes=10),"items":result[:100]})
    return _cache["items"]

@router.get("/api/news")
def news(category: str="All", state: str="", q: str="", limit: int=30):
    rows = _items()
    if category == "Maharashtra":
        rows = [x for x in rows if x.get("state")=="Maharashtra" or "bombay high court" in (x["title"]+" "+x["snippet"]).lower()]
    elif category in ("Supreme Court","High Courts","Legislation","Government"):
        rows = [x for x in rows if x["category"]==category]
    if state:
        needle=state.lower()
        rows=[x for x in rows if (x.get("state") or "").lower()==needle or needle in (x["title"]+" "+x["snippet"]).lower()]
    if q:
        needle=q.lower()
        rows=[x for x in rows if needle in (x["title"]+" "+x["snippet"]+" "+x["publisher"]).lower()]
    return {"items":rows[:max(1,min(limit,60))],"count":len(rows),"updated_at":datetime.now(timezone.utc).isoformat(),"states":STATES,"disclaimer":"Syndicated headlines and snippets are not verified legal reporting. Verify all legal propositions against the relevant official court or government record."}

class AnalysisRequest(BaseModel):
    title: str
    publisher: str = "Unknown"
    snippet: str = ""
    url: str
    language: str = "English"

@router.post("/api/news/analyze")
def analyze(body: AnalysisRequest):
    if not body.title.strip() or not body.url.startswith("https://"): raise HTTPException(400,"A headline and secure source link are required.")
    prompt = ("Analyze this Indian legal-news item using ONLY the supplied headline and snippet. Do not claim to have read the full article or judgment.\n"
      +"Headline: "+body.title+"\nPublisher: "+body.publisher+"\nSnippet: "+body.snippet[:1500]+"\nSource: "+body.url+"\n"
      +"Provide concise summary, likely legal issue if inferable, practical relevance, what remains unverified, and official primary sources to check. Never invent a case name, citation, judge, section, holding, date or legal effect. Label inference and uncertainty. This is legal information, not advice.")
    key=os.getenv("GEMINI_API_KEY")
    if not key: raise HTTPException(503,"AI analysis requires GEMINI_API_KEY configured in Railway.")
    try:
        from google import genai
        client=genai.Client(api_key=key)
        response=client.models.generate_content(model=os.getenv("GEMINI_MODEL","gemini-3.8-flash"),contents=prompt)
        answer=response.text or "AI returned no analysis."
    except Exception as exc: raise HTTPException(502,"AI analysis is temporarily unavailable: "+str(exc)[:180])
    return {"analysis":answer,"scope_note":"Analysis is based only on the supplied headline/snippet, not the full article or primary court record.","source_url":body.url}
