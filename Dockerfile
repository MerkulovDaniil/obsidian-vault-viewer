FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir uv && uv pip install --system silmaril

COPY docs/ /docs-pristine/
COPY docs/ /docs/
COPY playground.py .

ENV RESET_DIR=/docs-pristine

CMD uvicorn playground:app --host 0.0.0.0 --port ${PORT:-8080}
