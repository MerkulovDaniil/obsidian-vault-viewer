FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY docs/ /docs/
COPY silmaril.yml /app/silmaril.yml

# Always install latest silmaril on container start
CMD uv pip install --system --upgrade silmaril && silmaril --vault /docs --port ${PORT:-8080} --host 0.0.0.0
