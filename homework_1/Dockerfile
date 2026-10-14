FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 app

COPY contracts.py law_matcher.py link_extractor.py number_parser.py main.py ./
COPY law_aliases.json .

# индекс законов собирается один раз при сборке образа, контейнер стартует за доли секунды
RUN python -c "import law_matcher; law_matcher.build_cache()"

USER app
EXPOSE 8978

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8978/health').status == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8978"]
