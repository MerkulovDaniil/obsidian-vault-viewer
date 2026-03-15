---
status: active
tags:
  - docs
  - setup
---

# Configuration

## CLI arguments

```bash
silmaril --vault /path/to/vault --port 8000 --host 0.0.0.0 --title "My Notes"
```

| Argument  | Env variable | Default     | Description                    |
|-----------|-------------|-------------|--------------------------------|
| `--vault` | `VAULT_ROOT`| `./vault`   | Path to your Obsidian vault    |
| `--host`  | `VAULT_HOST`| `0.0.0.0`   | Bind address                   |
| `--port`  | `VAULT_PORT`| `8000`      | Bind port                      |
| `--title` | `VAULT_NAME`| folder name | App title in the sidebar       |

## Config file

Place `silmaril.yml` in the working directory:

```yaml
vault: /path/to/vault
host: 0.0.0.0
port: 8000
title: My Vault

# Appearance
favicon: https://example.com/icon.png
custom_css: "body { font-size: 18px; }"
custom_head: '<script src="..."></script>'

# Behavior
pinch_zoom: true      # allow pinch-to-zoom on mobile
readonly: false       # disable edit/delete
hide:                 # glob patterns to hide from sidebar
  - "_private/**"
  - "*.tmp"
  - "drafts/**"
```

**Priority**: CLI args > config file > environment variables > defaults.

## Clean URLs

Every file has a clean URL:

- `/notes/ideas.md` — view
- `/notes/ideas.md?edit` — edit
- `/notes/ideas.md?raw` — raw text

No `/view/` prefix needed.

## Authentication

Silmaril has no built-in auth. Recommended options:

1. **Cloudflare Access / Tunnel** — zero-trust, best for public hosting
2. **Reverse proxy with basic auth** — nginx, caddy
3. **Bind locally** — `silmaril --host 127.0.0.1`
