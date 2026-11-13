FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-mar && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y torch torchvision torchaudio || true
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY scripts/install_offline_models.py /tmp/install_offline_models.py
RUN python /tmp/install_offline_models.py
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
