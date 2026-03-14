# Obsidian Vault Viewer

A self-hosted, mobile-first web UI for browsing Obsidian vaults. Inspired by [notion4ever](https://github.com/MerkulovDaniil/notion4ever).

![Screenshot](screenshot.png)
<!-- Replace screenshot.png with an actual screenshot of your vault -->

## Features

- **Markdown rendering** with full Obsidian flavor: `[[wiki-links]]`, `![[embeds]]`, callouts, highlights, checkboxes
- **KaTeX** math rendering (`$inline$` and `$$display$$`)
- **Obsidian Bases** (`.base` files) with cards, list, and table views
- **Iconic plugin** support (Lucide icons and emoji per file/folder)
- **Cover images** from frontmatter (`banner`, `cover`, `image`)
- **Frontmatter badges** (status, tags) with color coding
- **Full-text search** with instant sidebar filtering and content snippets
- **Cards / List / Table views** for any directory
- **Mobile-first** responsive design
- **Edit and delete** notes in the browser
- **Code blocks** with copy button
- **File tree** sidebar with collapsible folders

## Quick Start

```bash
pip install -r requirements.txt
python app.py --vault /path/to/your/vault
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### One-liner

```bash
pip install fastapi uvicorn python-frontmatter markdown pyyaml && python app.py --vault /path/to/vault
```

## Configuration

| CLI argument | Env variable   | Default      | Description                      |
|-------------|----------------|--------------|----------------------------------|
| `--vault`   | `VAULT_ROOT`   | `./vault`    | Path to your Obsidian vault      |
| `--host`    | `VAULT_HOST`   | `0.0.0.0`    | Bind address                     |
| `--port`    | `VAULT_PORT`   | `8000`       | Bind port                        |
| `--title`   | `VAULT_NAME`   | folder name  | App title shown in the sidebar   |

CLI arguments take precedence over environment variables.

## Authentication

The app itself has **no built-in authentication**. Recommended options:

1. **Cloudflare Access / Tunnel** (recommended for public hosting) -- zero-trust access in front of the app
2. **Reverse proxy with basic auth** (nginx, caddy)
3. **Run locally** -- bind to `127.0.0.1` with `--host 127.0.0.1`

## Deployment

### systemd

```ini
[Unit]
Description=Obsidian Vault Viewer
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/vault-viewer/app.py --vault /path/to/vault --port 8000
WorkingDirectory=/opt/vault-viewer
Restart=always

[Install]
WantedBy=multi-user.target
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8000
CMD ["python", "app.py", "--vault", "/vault"]
```

```bash
docker run -v /path/to/vault:/vault -p 8000:8000 vault-viewer
```

## Russian / Русский

See [README_RU.md](README_RU.md) for documentation in Russian.

## License

[MIT](LICENSE)

## Credits

Inspired by [notion4ever](https://github.com/MerkulovDaniil/notion4ever). Built with [FastAPI](https://fastapi.tiangolo.com/), [python-frontmatter](https://github.com/eyeseast/python-frontmatter), and [KaTeX](https://katex.org/).

Author: [Daniil Merkulov](https://github.com/MerkulovDaniil)
