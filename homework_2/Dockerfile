FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RSS_DB_PATH=/srv/data/news.db
COPY requirements.txt requirements.role4.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements.role4.txt
COPY app/ ./app/
RUN mkdir -p /srv/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
