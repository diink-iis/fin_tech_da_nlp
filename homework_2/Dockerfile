FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RSS_DB_PATH=/srv/data/news.db HF_HOME=/srv/models/hf-cache \
    VENV_DIR=/usr/local HOST=0.0.0.0 PORT=8000
COPY requirements.txt ./
RUN pip install --no-cache-dir 'torch>=2.2' --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY run.sh ./
RUN mkdir -p /srv/data /srv/models/hf-cache
EXPOSE 8000
CMD ["./run.sh", "--offline"]
