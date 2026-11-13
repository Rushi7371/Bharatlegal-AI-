"""Pre-download public, ungated OPUS-MT translation models for offline runtime use."""
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

root = Path("/app/models")
root.mkdir(parents=True, exist_ok=True)
models = (
    "Helsinki-NLP/opus-mt-en-hi",
    "Helsinki-NLP/opus-mt-hi-en",
    "Helsinki-NLP/opus-mt-en-mr",
    "Helsinki-NLP/opus-mt-mr-en",
)
for model_id in models:
    destination = root / model_id.rsplit("/", 1)[-1]
    print(f"Downloading public translation model: {model_id}", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id, cache_dir=str(root))
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id, cache_dir=str(root))
    destination.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(destination)
    model.save_pretrained(destination, safe_serialization=True)
    del tokenizer, model
    print(f"Saved {model_id} to {destination}", flush=True)
