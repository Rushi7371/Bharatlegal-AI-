"""Server-side BHASHINI integration for BharatLegal AI.

Credentials and pipeline IDs are supplied only through Railway environment variables.
No document is sent to BHASHINI implicitly: callers must explicitly use these endpoints.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/bhashini", tags=["BHASHINI"])
_CONFIG_URL = os.getenv("BHASHINI_PIPELINE_CONFIG_URL", "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline")
_COMPUTE_URL = os.getenv("BHASHINI_COMPUTE_URL", "https://dhruva-api.bhashini.gov.in/services/inference/pipeline")
_TIMEOUT = max(5, min(int(os.getenv("BHASHINI_TIMEOUT_SECONDS", "45")), 120))
_CONFIG_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_LOCK = threading.Lock()
_LANG = {"English": "en", "Hindi": "hi", "Marathi": "mr", "en": "en", "hi": "hi", "mr": "mr"}

class TranslateInput(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    source_language: str = "en"
    target_language: str = "mr"

class SpeechInput(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    language: str = "mr"
    gender: str = "female"

class AudioInput(BaseModel):
    audio_base64: str = Field(min_length=8, max_length=20_000_000)
    language: str = "mr"
    audio_format: str = "wav"
    sampling_rate: int = Field(default=16000, ge=8000, le=48000)

def _credentials() -> tuple[str, str, str]:
    user_id = os.getenv("BHASHINI_USER_ID", "").strip()
    api_key = os.getenv("BHASHINI_API_KEY", "").strip()
    pipeline_id = os.getenv("BHASHINI_PIPELINE_ID", "").strip()
    if not user_id or not api_key or not pipeline_id:
        raise HTTPException(
            status_code=503,
            detail="BHASHINI is not configured yet. Add BHASHINI_USER_ID, BHASHINI_API_KEY and the approved BHASHINI_PIPELINE_ID to the Railway service variables."
        )
    return user_id, api_key, pipeline_id

def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read(12 * 1024 * 1024)
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("Unexpected BHASHINI response")
            return parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", "replace")
        print(f"BHASHINI HTTP error {exc.code}: {detail[:500]}", flush=True)
        raise HTTPException(status_code=502, detail=f"BHASHINI request failed (HTTP {exc.code}). Check the account, pipeline and service permissions.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(f"BHASHINI connection error: {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="BHASHINI could not be reached or returned an invalid response. Please retry.") from exc

def _task_config(task: str, source: str, target: str | None = None) -> dict:
    language = {"sourceLanguage": source}
    if target:
        language["targetLanguage"] = target
    return {"taskType": task, "config": {"language": language}}

def _get_pipeline(task: str, source: str, target: str | None = None) -> tuple[dict, str, str]:
    user_id, api_key, pipeline_id = _credentials()
    cache_key = (task, source, target or "")
    with _LOCK:
        cached = _CONFIG_CACHE.get(cache_key)
    if cached:
        return cached, user_id, api_key
    payload = {
        "pipelineTasks": [{"taskType": task}],
        "pipelineRequestConfig": {"pipelineId": pipeline_id}
    }
    config = _post_json(_CONFIG_URL, payload, {"userID": user_id, "ulcaApiKey": api_key})
    endpoint = config.get("pipelineInferenceAPIEndPoint") or config.get("pipelineInferenceAPIEndpoint") or {}
    callback = endpoint.get("callbackUrl") or endpoint.get("callbackURL")
    auth = endpoint.get("inferenceApiKey") or {}
    auth_name, auth_value = auth.get("name"), auth.get("value")
    responses = config.get("pipelineResponseConfig") or config.get("pipelineResponse") or []
    service_id = None
    for item in responses:
        if str(item.get("taskType", "")).lower() == task:
            for candidate in item.get("config") or []:
                lang = candidate.get("language") or {}
                if lang.get("sourceLanguage") == source and (not target or lang.get("targetLanguage") == target):
                    service_id = candidate.get("serviceId")
                    break
            if service_id:
                break
    if not callback or not auth_name or not auth_value or not service_id:
        raise HTTPException(status_code=502, detail=f"BHASHINI did not return an approved {task} service for this language selection. Check the pipeline's supported languages/tasks.")
    result = {"callback": callback, "auth_name": auth_name, "auth_value": auth_value, "service_id": service_id}
    with _LOCK:
        _CONFIG_CACHE[cache_key] = result
    return result, user_id, api_key

def _compute(task: str, source: str, target: str | None, input_data: dict, task_config_extra: dict | None = None) -> dict:
    cfg, _, _ = _get_pipeline(task, source, target)
    language = {"sourceLanguage": source}
    if target:
        language["targetLanguage"] = target
    task_config = {"language": language, "serviceId": cfg["service_id"]}
    if task_config_extra:
        task_config.update(task_config_extra)
    payload = {
        "pipelineTasks": [{"taskType": task, "config": task_config}],
        "inputData": input_data
    }
    result = _post_json(cfg["callback"], payload, {cfg["auth_name"]: cfg["auth_value"]})
    outputs = result.get("pipelineResponse") or []
    if not outputs:
        raise HTTPException(status_code=502, detail="BHASHINI returned no pipeline output.")
    return outputs[0]

def _lang(value: str) -> str:
    code = _LANG.get(value, value)
    if code not in {"en", "hi", "mr"}:
        raise HTTPException(status_code=400, detail="Supported languages are English (en), Hindi (hi) and Marathi (mr).")
    return code

@router.get("/status")
def bhashini_status():
    present = all(os.getenv(k, "").strip() for k in ("BHASHINI_USER_ID", "BHASHINI_API_KEY", "BHASHINI_PIPELINE_ID"))
    return {
        "provider": "BHASHINI",
        "configured": present,
        "enabled": os.getenv("BHASHINI_ENABLED", "false").lower() == "true" and present,
        "supported_languages": ["English", "Hindi", "Marathi"],
        "features": ["translation", "speech_to_text", "text_to_speech"],
        "note": "API credentials and an approved pipeline ID are required. OCR and automatic document translation remain behind the existing document workflow until separately validated."
    }

@router.post("/translate")
def bhashini_translate(body: TranslateInput):
    if os.getenv("BHASHINI_ENABLED", "false").lower() != "true":
        raise HTTPException(status_code=503, detail="BHASHINI integration is installed but disabled until credentials, pipeline and permission are verified.")
    source, target = _lang(body.source_language), _lang(body.target_language)
    if source == target:
        return {"provider": "BHASHINI", "source_language": source, "target_language": target, "translated_text": body.text}
    output = _compute("translation", source, target, {"input": [{"source": body.text}]})
    values = output.get("output") or []
    translated = str(values[0].get("target") or "").strip() if values else ""
    if not translated:
        raise HTTPException(status_code=502, detail="BHASHINI returned an empty translation; no success was reported.")
    return {"provider": "BHASHINI", "source_language": source, "target_language": target, "original_text": body.text, "translated_text": translated}

@router.post("/tts")
def bhashini_tts(body: SpeechInput):
    if os.getenv("BHASHINI_ENABLED", "false").lower() != "true":
        raise HTTPException(status_code=503, detail="BHASHINI integration is installed but disabled until credentials, pipeline and permission are verified.")
    language = _lang(body.language)
    output = _compute("tts", language, None, {"input": [{"source": body.text}]}, {"gender": body.gender})
    audio = output.get("audio") or {}
    encoded = audio.get("audioContent")
    if not encoded:
        raise HTTPException(status_code=502, detail="BHASHINI did not return audio content.")
    return {"provider": "BHASHINI", "language": language, "audio_base64": encoded, "audio_format": "wav"}

@router.post("/asr")
def bhashini_asr(body: AudioInput):
    if os.getenv("BHASHINI_ENABLED", "false").lower() != "true":
        raise HTTPException(status_code=503, detail="BHASHINI integration is installed but disabled until credentials, pipeline and permission are verified.")
    language = _lang(body.language)
    try:
        audio_bytes = base64.b64decode(body.audio_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Audio must be valid base64.") from exc
    if not audio_bytes or len(audio_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio must be smaller than 10 MB.")
    output = _compute("asr", language, None, {"input": [{"source": None}], "audio": [{"audioContent": body.audio_base64}]},
                      {"audioFormat": body.audio_format, "samplingRate": body.sampling_rate})
    values = output.get("output") or []
    transcript = str(values[0].get("source") or "").strip() if values else ""
    if not transcript:
        raise HTTPException(status_code=502, detail="BHASHINI returned an empty transcript.")
    return {"provider": "BHASHINI", "language": language, "transcript": transcript}
