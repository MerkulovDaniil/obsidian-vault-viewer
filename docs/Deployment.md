---
status: active
tags:
  - docs
  - setup
---

# Deployment

## pip

```bash
pip install silmaril
silmaril --vault /path/to/vault
```

## From source

```bash
git clone https://github.com/MerkulovDaniil/silmaril.git
cd silmaril
pip install .
silmaril --vault /path/to/vault
```

## systemd

```ini
[Unit]
Description=Silmaril
After=network.target

[Service]
ExecStart=silmaril --vault /path/to/vault --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

## Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml app.py ./
COPY silmaril/ silmaril/
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["silmaril", "--vault", "/vault"]
```

```bash
docker run -v /path/to/vault:/vault -p 8000:8000 silmaril
```

## Resources

Silmaril is lightweight:

| Metric | Value |
|--------|-------|
| RAM | ~70 MB |
| Disk | ~130 KB (code) |
| CPU | ~0% idle |
| Processes | 1 |
| Dependencies | 5 Python packages |
