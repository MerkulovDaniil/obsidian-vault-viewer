FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml app.py ./
COPY silmaril/ silmaril/
RUN pip install --no-cache-dir .

COPY docs/ /docs-pristine/
COPY docs/ /docs/
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8080
CMD ["/start.sh"]
