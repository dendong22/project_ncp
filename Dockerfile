FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modules/ ./modules/
COPY prompts/ ./prompts/
COPY data/corpus/ ./data/corpus/
COPY data/index/ ./data/index/
COPY demo/ ./demo/
COPY pipeline.py ui_theme.py app_v2.py ./

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "app_v2.py"]
