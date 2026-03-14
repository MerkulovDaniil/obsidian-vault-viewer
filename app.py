"""
Obsidian Vault Viewer — self-hosted, mobile-first web UI.
Notion4ever-inspired design. Cards/list/table database views.
"""

import os
import re
import sys
import argparse
import mimetypes
from pathlib import Path

import frontmatter
import markdown
from fastapi import FastAPI, Request, Response, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

# --- Config ---
VAULT_ROOT = Path(os.environ.get("VAULT_ROOT", "./vault"))
HOST = os.environ.get("VAULT_HOST", "0.0.0.0")
PORT = int(os.environ.get("VAULT_PORT", "8000"))
APP_TITLE = os.environ.get("VAULT_NAME", "")

app = FastAPI(docs_url=None, redoc_url=None)


def safe_path(rel: str) -> Path:
    p = (VAULT_ROOT / rel).resolve()
    if not str(p).startswith(str(VAULT_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    return p


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- Icons from Obsidian Iconic plugin ---

import json

_icon_cache = None

def load_icons() -> dict:
    """Load icon mappings from Iconic plugin."""
    global _icon_cache
    if _icon_cache is not None:
        return _icon_cache
    iconic_path = VAULT_ROOT / ".obsidian" / "plugins" / "iconic" / "data.json"
    if not iconic_path.exists():
        _icon_cache = {}
        return _icon_cache
    try:
        data = json.loads(iconic_path.read_text(encoding="utf-8"))
        icons = {}
        for section in ("fileIcons", "folderIcons"):
            for path, info in data.get(section, {}).items():
                icon = info.get("icon", "")
                color = info.get("color", "")
                if icon:
                    icons[path] = {"icon": icon, "color": color}
        _icon_cache = icons
    except Exception:
        _icon_cache = {}
    return _icon_cache


def get_icon_html(rel_path: str, fallback: str = "&#128196;") -> str:
    """Get icon HTML for a vault path. Supports emoji and lucide icons."""
    icons = load_icons()
    info = icons.get(rel_path, {})
    icon = info.get("icon", "")
    color = info.get("color", "")
    style = f' style="color:{color}"' if color else ""

    if not icon:
        return f'<span class="icon">{fallback}</span>'
    # Emoji (not lucide-)
    if not icon.startswith("lucide-"):
        return f'<span class="icon"{style}>{icon}</span>'
    # Lucide icon
    name = icon.replace("lucide-", "")
    return f'<i data-lucide="{name}" class="lucide-icon"{style}></i>'


# --- File tree ---

def get_file_tree(root: Path) -> list[dict]:
    items = []
    try:
        entries = sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return items
    for entry in entries:
        if entry.name.startswith("."):
            continue
        rel = str(entry.relative_to(VAULT_ROOT))
        if entry.is_dir():
            children = get_file_tree(entry)
            if children:
                items.append({"name": entry.name, "path": rel, "type": "dir", "children": children})
        elif entry.is_file():
            items.append({"name": entry.name, "path": rel, "type": "file"})
    return items


# --- Markdown rendering ---

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}


def render_embeds(text: str) -> str:
    """Convert ![[image.png]] and ![[note]] embeds to HTML."""
    def replace_embed(m):
        target = m.group(1)
        # Check if it's an image
        ext = Path(target).suffix.lower()
        if ext in IMG_EXTS:
            url = resolve_img(f"[[{target}]]")
            if url:
                return f'<img src="{url}" alt="{_escape(target)}" loading="lazy" style="max-width:100%;border-radius:4px;">'
            return f'<em>[image not found: {_escape(target)}]</em>'
        # Non-image embed (note transclusion) — link to it
        href = f"/view/{target}" if target.endswith(".md") else f"/view/{target}.md"
        return f'<a href="{href}" class="wikilink">&#128196; {_escape(target)}</a>'
    return re.sub(r'!\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', replace_embed, text)


_wikilink_cache: dict[str, str] = {}

def _resolve_wikilink(target: str) -> str:
    """Resolve a wiki-link target to a vault path, searching like Obsidian."""
    if target in _wikilink_cache:
        return _wikilink_cache[target]
    # Exact path
    t = target if target.endswith(".md") else target + ".md"
    if (VAULT_ROOT / t).exists():
        _wikilink_cache[target] = f"/view/{t}"
        return _wikilink_cache[target]
    # Search vault by filename
    name = Path(t).name
    for fp in VAULT_ROOT.rglob(name):
        rel = str(fp.relative_to(VAULT_ROOT))
        _wikilink_cache[target] = f"/view/{rel}"
        return _wikilink_cache[target]
    # Not found — link anyway
    _wikilink_cache[target] = f"/view/{t}"
    return _wikilink_cache[target]


def render_wiki_links(text: str) -> str:
    def replace_link(m):
        target = m.group(1)
        display = m.group(2) if m.group(2) else target
        href = _resolve_wikilink(target)
        return f'<a href="{href}" class="wikilink">{display}</a>'
    return re.sub(r'\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]', replace_link, text)


def render_autolinks(text: str) -> str:
    """Convert bare URLs to clickable links."""
    return re.sub(
        r'(?<!["\(=])(?<!\]\()(?<!\w)(https?://[^\s<>\)\]]+)',
        r'<a href="\1">\1</a>',
        text
    )


def render_callouts(text: str) -> str:
    icons = {
        "note": "&#9998;&#65039;", "tip": "&#128161;", "hint": "&#128161;",
        "important": "&#128161;", "info": "&#8505;&#65039;",
        "warning": "&#9888;&#65039;", "caution": "&#9888;&#65039;",
        "danger": "&#9889;", "error": "&#9889;", "bug": "&#128027;",
        "example": "&#128203;", "quote": "&#10077;", "cite": "&#10077;",
        "success": "&#9989;", "check": "&#9989;", "done": "&#9989;",
        "question": "&#10067;", "todo": "&#9744;",
    }
    lines = text.split("\n")
    result, body, c_type, c_title = [], [], "", ""
    in_c = False

    def flush():
        nonlocal in_c, body
        if not in_c:
            return
        icon = icons.get(c_type.lower(), "&#128221;")
        bhtml = markdown.markdown("\n".join(body), extensions=['tables', 'fenced_code', 'sane_lists'])
        result.append(
            f'<div class="callout callout-{c_type.lower()}">'
            f'<div class="callout-title">{icon} {c_title or c_type.capitalize()}</div>'
            f'<div class="callout-body">{bhtml}</div></div>')
        in_c = False
        body = []

    for line in lines:
        m = re.match(r'^>\s*\[!(\w+)\]\s*(.*)', line)
        if m:
            flush()
            in_c = True
            c_type, c_title = m.group(1), m.group(2).strip()
            continue
        if in_c and line.startswith(">"):
            body.append(line[1:].lstrip(" "))
            continue
        if in_c and line.strip() == "":
            body.append("")
            continue
        flush()
        result.append(line)
    flush()
    return "\n".join(result)


_math_store: list[str] = []

def _protect_math(content: str) -> str:
    """Replace $$...$$ and $...$ with placeholders to protect from markdown."""
    _math_store.clear()
    def stash(m):
        _math_store.append(m.group(0))
        return f'⟨MATH:{len(_math_store)-1}⟩'
    # Display math first (greedy across lines)
    content = re.sub(r'\$\$(.+?)\$\$', stash, content, flags=re.DOTALL)
    # Inline math (not greedy, single line)
    content = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', stash, content)
    return content

def _restore_math(html: str) -> str:
    """Restore math placeholders."""
    for i, orig in enumerate(_math_store):
        html = html.replace(f'⟨MATH:{i}⟩', orig)
    return html

def render_md(content: str) -> str:
    content = _protect_math(content)  # protect LaTeX from markdown
    content = render_embeds(content)
    content = render_callouts(content)
    content = render_wiki_links(content)
    content = render_autolinks(content)
    content = re.sub(r'==(.*?)==', r'<mark>\1</mark>', content)
    content = re.sub(r'(?<!\w)#([a-zA-Z0-9_/\u0400-\u04FF\-]+)', r'⟨TAG:\1⟩', content)
    html = markdown.markdown(content, extensions=[
        'tables', 'fenced_code', 'codehilite', 'toc', 'nl2br', 'sane_lists', 'smarty'
    ])
    html = re.sub(r'⟨TAG:(.+?)⟩', r'<span class="tag">#\1</span>', html)
    html = _restore_math(html)  # put LaTeX back
    html = html.replace("<table", '<div class="table-wrap"><table').replace("</table>", "</table></div>")
    html = html.replace("[ ]", '<input type="checkbox">')
    html = html.replace("[x]", '<input type="checkbox" checked>')
    html = html.replace("[X]", '<input type="checkbox" checked>')
    return html


# --- Frontmatter / page header ---

COVER_FIELDS = {"banner", "cover", "image", "cover_image", "header_image"}
BADGE_FIELDS = {"status", "tags", "tag", "labels", "label", "category", "categories"}
SKIP_FIELDS = {"cssclass", "cssclasses", "type", "publish", "aliases"}


def resolve_img(val: str) -> str:
    if not val:
        return ""
    val = str(val).strip()
    m = re.match(r'\[\[(.+?)\]\]', val)
    if m:
        for fp in VAULT_ROOT.rglob(m.group(1)):
            return f"/static/{fp.relative_to(VAULT_ROOT)}"
        return ""
    if val.startswith("http"):
        return val
    if (VAULT_ROOT / val).exists():
        return f"/static/{val}"
    return ""


def status_color(s: str) -> str:
    s = s.lower().strip()
    if s in ("active", "in progress", "wip"):
        return "green"
    if s in ("frozen", "paused", "on hold", "waiting"):
        return "blue"
    if s in ("done", "completed", "finished", "closed"):
        return "gray"
    if s in ("blocked", "error", "failed", "critical"):
        return "red"
    return "default"


def parse_meta(fp: Path) -> dict:
    """Parse frontmatter from a file, return metadata dict."""
    try:
        post = frontmatter.load(fp)
        return dict(post.metadata)
    except Exception:
        return {}


def get_page_parts(meta: dict, file_path: str = "") -> dict:
    """Extract structured page parts: cover, icon, badges, props."""
    result = {"cover": "", "icon": "", "badges": "", "props": ""}
    if not meta:
        return result

    # Cover
    for f in COVER_FIELDS:
        if f in meta and meta[f]:
            url = resolve_img(str(meta[f]))
            if url:
                result["cover"] = f'<div class="cover"><img src="{url}" alt="" loading="lazy"></div>'
                break

    # Icon from Iconic plugin
    if file_path:
        icon_html = get_icon_html(file_path, "")
        if icon_html:
            result["icon"] = f'<div class="page-icon">{icon_html}</div>'

    # Badges
    badges = []
    for f in ("status",):
        vals = meta.get(f, [])
        if isinstance(vals, str):
            vals = [vals]
        for v in (vals if isinstance(vals, list) else []):
            c = status_color(str(v))
            badges.append(f'<span class="badge badge-{c}">{v}</span>')
    for f in ("tags", "tag", "labels", "category"):
        vals = meta.get(f, [])
        if isinstance(vals, str):
            vals = [vals]
        for v in (vals if isinstance(vals, list) else []):
            badges.append(f'<span class="tag">{v}</span>')
    if badges:
        result["badges"] = f'<div class="badges">{"".join(badges)}</div>'

    # Properties
    shown = COVER_FIELDS | BADGE_FIELDS | SKIP_FIELDS
    props = {k: v for k, v in meta.items() if k.lower() not in shown and v is not None and str(v).strip()}
    if props:
        rows = []
        for k, v in props.items():
            val = str(v)[:300]
            rows.append(f'<tr><td class="pk">{_escape(k)}</td><td class="pv">{_escape(val)}</td></tr>')
        result["props"] = f'<div class="props"><table>{"".join(rows)}</table></div>'

    return result


# --- Obsidian Bases (.base) engine ---

import yaml


def parse_base_file(fp: Path) -> dict:
    """Parse a .base YAML file."""
    try:
        return yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _eval_filter(condition: str, meta: dict, fp: Path) -> bool:
    """Evaluate a single Obsidian Base filter condition against a file."""
    condition = condition.strip()

    # file.folder != "xxx"
    m = re.match(r'file\.folder\s*!=\s*"(.+?)"', condition)
    if m:
        return str(fp.parent.relative_to(VAULT_ROOT)) != m.group(1)

    # file.folder == "xxx" or file.inFolder("xxx")
    m = re.match(r'file\.folder\s*==\s*"(.+?)"', condition) or re.match(r'file\.inFolder\("(.+?)"\)', condition)
    if m:
        return m.group(1) in str(fp.parent.relative_to(VAULT_ROOT))

    # file.name.startsWith("xxx")
    m = re.match(r'file\.name\.startsWith\("(.+?)"\)', condition)
    if m:
        return fp.stem.startswith(m.group(1))

    # file.ext == "xxx"
    m = re.match(r'file\.ext\s*==\s*"(.+?)"', condition)
    if m:
        return fp.suffix.lstrip(".") == m.group(1)

    # file.tags.contains("xxx")
    m = re.match(r'file\.tags\.contains\("(.+?)"\)', condition)
    if m:
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        return m.group(1) in (tags if isinstance(tags, list) else [])

    # property == value  (e.g. type == "project", status == ["finished"])
    m = re.match(r'(\w+)\s*==\s*(.+)', condition)
    if m:
        key, val_str = m.group(1), m.group(2).strip()
        actual = meta.get(key, "")
        # Parse value
        if val_str.startswith('"') and val_str.endswith('"'):
            target = val_str.strip('"')
        elif val_str.startswith('['):
            try:
                target = yaml.safe_load(val_str)
            except Exception:
                target = val_str
        else:
            target = val_str

        if isinstance(actual, list) and isinstance(target, list):
            return any(t in actual for t in target)
        if isinstance(actual, list):
            return target in actual
        return str(actual) == str(target)

    # property != value
    m = re.match(r'(\w+)\s*!=\s*"(.+?)"', condition)
    if m:
        return str(meta.get(m.group(1), "")) != m.group(2)

    return True  # unknown filter → pass


def apply_filters(filters: dict, meta: dict, fp: Path) -> bool:
    """Apply nested AND/OR filter structure."""
    if not filters:
        return True
    if "and" in filters:
        return all(
            apply_filters(f, meta, fp) if isinstance(f, dict) else _eval_filter(f, meta, fp)
            for f in filters["and"]
        )
    if "or" in filters:
        return any(
            apply_filters(f, meta, fp) if isinstance(f, dict) else _eval_filter(f, meta, fp)
            for f in filters["or"]
        )
    return True


def collect_base_entries(global_filters: dict, view_filters: dict = None) -> list[dict]:
    """Collect all vault files matching base filters."""
    entries = []
    for fp in VAULT_ROOT.rglob("*.md"):
        if fp.name.startswith("."):
            continue
        meta = parse_meta(fp)
        if not apply_filters(global_filters, meta, fp):
            continue
        if view_filters and not apply_filters(view_filters, meta, fp):
            continue

        cover = ""
        for f in COVER_FIELDS:
            if f in meta and meta[f]:
                cover = resolve_img(str(meta[f]))
                if cover:
                    break
        status = meta.get("status", [])
        if isinstance(status, str):
            status = [status]
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        entries.append({
            "name": fp.stem,
            "path": str(fp.relative_to(VAULT_ROOT)),
            "cover": cover,
            "status": status if isinstance(status, list) else [],
            "tags": tags if isinstance(tags, list) else [],
            "meta": meta,
            "mtime": fp.stat().st_mtime,
        })

    entries.sort(key=lambda e: e["name"].lower())
    return entries


def render_base_cards(entries: list[dict], image_field: str = "", aspect: float = 0.5) -> str:
    """Render entries as gallery cards."""
    cards = ""
    for e in entries:
        cover = e["cover"]
        # Try image field from .base config (e.g. "note.banner")
        if not cover and image_field:
            prop = image_field.replace("note.", "").replace("formula.", "")
            if prop in e["meta"] and e["meta"][prop]:
                cover = resolve_img(str(e["meta"][prop]))

        if cover:
            h = int(120 / aspect) if aspect else 120
            cover_html = f'<div class="card-cover" style="height:{min(h, 240)}px"><img src="{cover}" loading="lazy"></div>'
        else:
            cover_html = '<div class="card-cover" style="height:80px;background:var(--bg2);display:flex;align-items:center;justify-content:center;color:var(--text2);font-size:24px;">&#128196;</div>'

        badges_html = ""
        for s in e["status"][:2]:
            badges_html += f'<span class="badge badge-{status_color(str(s))}">{s}</span>'
        for t in e["tags"][:3]:
            badges_html += f'<span class="tag">{t}</span>'

        card_icon = get_icon_html(e["path"], "")
        cards += f'<div class="card"><a href="/view/{e["path"]}">{cover_html}<div class="card-body"><div class="card-title">{card_icon}{_escape(e["name"])}</div><div class="card-meta">{badges_html}</div></div></a></div>'
    return f'<div class="gallery">{cards}</div>'


def render_base_table(entries: list[dict], columns: list[str] = None) -> str:
    """Render entries as a table with specified columns."""
    if not columns:
        columns = ["status", "tags"]
    # Clean column names
    cols = [c.replace("file.", "").replace("note.", "") for c in columns if c != "file.name"]

    ths = '<th>Name</th>' + "".join(f'<th>{_escape(c)}</th>' for c in cols)
    trs = ""
    for e in entries:
        tds = f'<td><a href="/view/{e["path"]}">{_escape(e["name"])}</a></td>'
        for c in cols:
            if c == "status":
                cell = "".join(f'<span class="badge badge-{status_color(str(s))}">{s}</span>' for s in e["status"])
            elif c in ("tags", "tag"):
                cell = '<div class="cell-tags">' + "".join(f'<span class="tag">{t}</span>' for t in e["tags"]) + '</div>'
            else:
                val = e["meta"].get(c, "")
                cell = _escape(str(val))[:150]
            tds += f'<td>{cell}</td>'
        trs += f'<tr>{tds}</tr>'
    return f'<div class="table-wrap"><table class="db-table"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>'


# --- CSS (notion4ever inspired) ---

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {
    --bg: #ffffff;
    --bg2: #f7f7f5;
    --surface: #f0f0ee;
    --text: #37352f;
    --text2: #787774;
    --accent: #2383e2;
    --accent2: #9065e3;
    --green: #448361;
    --green-bg: #dbeddb;
    --blue: #527da5;
    --blue-bg: #d3e5ef;
    --red: #c4554d;
    --red-bg: #ffe2dd;
    --gray: #91918e;
    --gray-bg: #e3e2e0;
    --yellow: #c29243;
    --yellow-bg: #fdecc8;
    --border: #e9e9e7;
    --sidebar-w: 260px;
    --content-w: 800px;
    --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --serif: 'Georgia', 'Palatino Linotype', 'Times New Roman', serif;
    --mono: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { overflow-x: hidden; font-size: 16px; }
body {
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    min-height: 100vh;
    display: flex;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
}

/* === Sidebar === */
.sidebar {
    width: var(--sidebar-w);
    background: var(--bg2);
    border-right: 1px solid var(--border);
    height: 100vh; height: 100dvh;
    position: fixed; left: 0; top: 0;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior: contain;
    z-index: 100;
    transition: transform 0.2s;
    font-size: 14px;
}
.sidebar-hdr {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    font-weight: 600; font-size: 14px; color: var(--text2);
    display: flex; align-items: center; gap: 6px;
}
.sidebar-hdr a { color: var(--text); text-decoration: none; }
.sidebar-search { padding: 6px 10px; border-bottom: 1px solid var(--border); }
.sidebar-search input {
    width: 100%; padding: 5px 10px;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 5px; color: var(--text); font-size: 13px; outline: none;
}
.sidebar-search input:focus { border-color: var(--accent); }
.tree { padding: 4px 0; }
.tree-item {
    display: flex; align-items: center;
    padding: 3px 8px 3px calc(8px + var(--depth, 0) * 18px);
    cursor: pointer; font-size: 13px; color: var(--text2);
    text-decoration: none; border-radius: 4px; margin: 0 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tree-item:hover { background: var(--surface); color: var(--text); }
.tree-item.active { background: var(--surface); color: var(--accent); font-weight: 500; }
.tree-item .icon { margin-right: 5px; font-size: 13px; flex-shrink: 0; opacity: 0.7; display: inline-flex; }
.tree-item .lucide-icon, .lucide-icon { width: 16px; height: 16px; flex-shrink: 0; margin-right: 5px; opacity: 0.7; }
.card-title .lucide-icon { width: 14px; height: 14px; margin-right: 4px; vertical-align: -2px; }
.card-title .icon { margin-right: 4px; }
.tree-dir > .tree-children { display: none; }
.tree-dir.open > .tree-children { display: block; }
.tree-dir > .tree-item .chv {
    margin-right: 3px; transition: transform 0.15s; font-size: 9px; opacity: 0.5;
}
.tree-dir.open > .tree-item .chv { transform: rotate(90deg); }
.sidebar.hidden { transform: translateX(-100%); }

/* === Main === */
.main-wrapper {
    margin-left: var(--sidebar-w);
    flex: 1; min-height: 100vh;
    display: flex; justify-content: center;
    background: var(--bg);
}
.main {
    width: 100%;
    max-width: var(--content-w);
    padding: 12px 32px 40px;
    overflow-wrap: break-word; word-wrap: break-word; min-width: 0;
}
.breadcrumb {
    font-size: 12px; color: var(--text2); margin-bottom: 4px;
    padding: 6px 0;
}
.breadcrumb a { color: var(--text2); text-decoration: none; }
.breadcrumb a:hover { color: var(--text); }
.breadcrumb span.sep { margin: 0 4px; color: var(--border); }

/* === Cover + Icon (notion4ever style) === */
.cover {
    max-height: 200px; overflow: hidden;
    /* break out of .main to full width of .main-wrapper */
    width: 100vw; position: relative;
    left: 50%; right: 50%;
    margin-left: -50vw; margin-right: -50vw;
    margin-bottom: 0;
}
.cover img { width: 100%; height: 200px; object-fit: cover; display: block; }
.page-icon {
    font-size: 78px; line-height: 78px;
    margin-top: -42px; margin-bottom: 4px;
    position: relative; z-index: 1;
}
.page-icon .lucide-icon { width: 72px; height: 72px; }
.page-icon img { width: 78px; height: 78px; object-fit: cover; border-radius: 8px; }
.no-cover .page-icon { margin-top: 0; }

/* === Badges === */
.badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.badge {
    padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 500;
}
.badge-green { background: var(--green-bg); color: var(--green); }
.badge-blue { background: var(--blue-bg); color: var(--blue); }
.badge-red { background: var(--red-bg); color: var(--red); }
.badge-gray { background: var(--gray-bg); color: var(--gray); }
.badge-default { background: var(--surface); color: var(--text2); }
.tag {
    background: var(--surface); color: var(--text2);
    padding: 1px 6px; border-radius: 3px; font-size: 12px; white-space: nowrap;
}

/* === Properties === */
.props {
    background: var(--bg2); border-radius: 6px; padding: 6px 12px;
    margin-bottom: 18px; font-size: 13px;
}
.props table { width: 100%; border-collapse: collapse; }
.props tr { border-bottom: 1px solid var(--border); }
.props tr:last-child { border-bottom: none; }
.props td { padding: 5px 6px; vertical-align: top; }
.pk { color: var(--text2); font-weight: 500; white-space: nowrap; width: 1%; padding-right: 12px; }
.pv { color: var(--text); word-break: break-word; }

/* === Markdown content === */
.md { overflow-wrap: break-word; word-break: break-word; font-family: var(--serif); font-size: 16px; line-height: 1.7; }
.md h1, .md h2, .md h3, .md h4, .md h5, .md h6 { font-family: var(--font); font-weight: 700; }
.md h1 { font-size: 1.875em; margin: 2em 0 0.5em; }
.md h2 { font-size: 1.5em; margin: 1.4em 0 0.4em; }
.md h3 { font-size: 1.25em; margin: 1.2em 0 0.3em; }
.md h4 { font-size: 1em; margin: 1em 0 0.3em; }
.md p { margin: 0.5em 0; }
.md a { color: var(--accent); text-decoration: none; }
.md a:hover { text-decoration: underline; }
.md a.wikilink { color: var(--accent2); text-decoration: underline; text-decoration-style: dotted; }
.md code {
    background: var(--surface); padding: 2px 5px; border-radius: 3px;
    font-family: var(--mono); font-size: 0.85em;
}
.md pre {
    background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; overflow-x: auto; margin: 1em 0; max-width: 100%;
    position: relative;
}
.md pre code { background: none; padding: 0; font-size: 13px; line-height: 1.5; }
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 1em 0; max-width: 100%; }
.md table { border-collapse: collapse; font-family: var(--font); font-size: 14px; min-width: 100%; width: max-content; }
.md th, .md td { white-space: nowrap; }
.md td { white-space: normal; min-width: 80px; }
.md th, .md td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
.md th { background: var(--bg2); font-weight: 600; font-size: 13px; color: var(--text2); }
.md blockquote {
    border-left: 3px solid var(--border); padding-left: 14px; margin: 1em 0;
    color: var(--text2); font-style: italic;
}
.md ul, .md ol { padding-left: 1.5em; margin: 0.5em 0; }
.md li { margin: 3px 0; }
.md img { max-width: 100%; height: auto; border-radius: 4px; }
.md hr { border: none; border-top: 1px solid var(--border); margin: 1.5em 0; }
.md mark { background: var(--yellow-bg); color: var(--text); padding: 0 3px; border-radius: 2px; }
.md input[type="checkbox"] { margin-right: 6px; cursor: pointer; width: 16px; height: 16px; vertical-align: middle; accent-color: var(--accent); }
.md li:has(> input[type="checkbox"]) { list-style: none; margin-left: -1.5em; }

/* === Callouts === */
.callout { border-radius: 6px; padding: 12px 16px; margin: 1em 0; border-left: 4px solid; }
.callout-title { font-family: var(--font); font-weight: 600; font-size: 14px; margin-bottom: 4px; }
.callout-body { font-size: 14px; }
.callout-body p { margin: 3px 0; }
.callout-note { border-color: var(--accent); background: #e8f0fe; }
.callout-tip, .callout-hint { border-color: #0d9488; background: #e6f6f4; }
.callout-warning, .callout-caution { border-color: var(--yellow); background: var(--yellow-bg); }
.callout-danger, .callout-error { border-color: var(--red); background: var(--red-bg); }
.callout-success, .callout-check, .callout-done { border-color: var(--green); background: var(--green-bg); }
.callout-quote, .callout-cite { border-color: var(--gray); background: var(--bg2); }
.callout-question { border-color: var(--yellow); background: var(--yellow-bg); }

/* === Toolbar === */
.toolbar { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
.btn {
    padding: 5px 14px; border-radius: 4px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--text); cursor: pointer; font-size: 13px;
    text-decoration: none; display: inline-flex; align-items: center; gap: 4px;
    font-family: var(--font);
}
.btn:hover { background: var(--surface); }
.btn-primary { background: var(--accent); color: white; border-color: var(--accent); }
.btn-primary:hover { opacity: 0.9; }
.btn.active { background: var(--surface); font-weight: 600; }

/* === Edit === */
.edit-area {
    width: 100%; min-height: 70vh;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 14px; font-family: var(--mono); font-size: 14px;
    line-height: 1.6; resize: vertical; outline: none;
}
.edit-area:focus { border-color: var(--accent); }

/* === Toast === */
.toast {
    position: fixed; bottom: 20px; right: 20px;
    background: var(--green); color: white;
    padding: 8px 18px; border-radius: 6px;
    font-size: 13px; font-weight: 500; z-index: 1000;
    animation: toast 2s ease-in-out;
}
@keyframes toast {
    0% { opacity: 0; transform: translateY(10px); }
    15% { opacity: 1; transform: translateY(0); }
    85% { opacity: 1; }
    100% { opacity: 0; }
}

/* === Gallery (cards view) === */
.db-toolbar {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
}
.view-tabs { display: flex; gap: 2px; }
.view-tab {
    padding: 4px 12px; font-size: 13px; color: var(--text2);
    cursor: pointer; border-radius: 4px; text-decoration: none;
    border: none; background: none; font-family: var(--font);
}
.view-tab:hover { background: var(--surface); color: var(--text); }
.view-tab.active { background: var(--surface); color: var(--text); font-weight: 500; }
.filter-info { font-size: 12px; color: var(--text2); }

.gallery {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
}
.card {
    border: 1px solid var(--border); border-radius: 6px;
    overflow: hidden; transition: box-shadow 0.15s;
    background: var(--bg);
}
.card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.card a { text-decoration: none; color: var(--text); display: block; }
.card-cover { height: 120px; overflow: hidden; background: var(--bg2); }
.card-cover img { width: 100%; height: 100%; object-fit: cover; }
.card-body { padding: 10px 12px; }
.card-title { font-size: 14px; font-weight: 500; margin-bottom: 4px; }
.card-meta { font-size: 12px; color: var(--text2); display: flex; flex-wrap: wrap; gap: 4px; }

/* List view */
.db-list { border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.db-row {
    display: flex; align-items: center; padding: 8px 14px;
    border-bottom: 1px solid var(--border); gap: 12px;
    text-decoration: none; color: var(--text); font-size: 14px;
}
.db-row:last-child { border-bottom: none; }
.db-row:hover { background: var(--bg2); }
.db-row-title { font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.db-row-status { flex-shrink: 0; }
.db-row-tags { flex-shrink: 0; display: flex; gap: 4px; }
.db-row-date { flex-shrink: 0; font-size: 12px; color: var(--text2); }

/* Table view */
.db-table { width: 100%; border-collapse: collapse; font-size: 13px; font-family: var(--font); }
.db-table th {
    text-align: left; padding: 6px 10px; font-weight: 500; font-size: 12px;
    color: var(--text2); border-bottom: 1px solid var(--border); background: var(--bg2);
    white-space: nowrap;
}
.db-table td { padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.db-table tr:hover { background: var(--bg2); }
.db-table a { color: var(--text); text-decoration: none; font-weight: 500; }
.db-table a:hover { color: var(--accent); }
.db-table .cell-tags { display: flex; gap: 3px; flex-wrap: wrap; }

/* === Search results (sidebar) === */
.search-results { padding: 0 8px; }
.sr-item {
    padding: 5px 8px; font-size: 12px; color: var(--text);
    cursor: pointer; border-radius: 4px; text-decoration: none; display: block;
}
.sr-item:hover { background: var(--surface); }
.sr-path { color: var(--text2); font-size: 11px; }
.sr-match { color: var(--yellow); font-size: 11px; }

/* === Home === */
.home-recent { margin-top: 16px; }
.home-recent h3 { font-size: 14px; font-weight: 600; color: var(--text2); margin-bottom: 8px; }

/* === Top bar === */
.topbar {
    display: flex; align-items: center; gap: 6px;
    padding: 0; margin: 0;
    position: sticky; top: 0; z-index: 10;
    background: var(--bg);
    min-height: 28px;
}
.topbar-toggle {
    background: none; border: none; color: var(--text2); cursor: pointer;
    padding: 4px 6px; border-radius: 4px; font-size: 18px; flex-shrink: 0;
    display: flex; align-items: center;
}
.topbar-toggle:hover { background: var(--surface); color: var(--text); }
.topbar-bc {
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 12px; color: var(--text2);
}
.topbar-bc a { color: var(--text2); text-decoration: none; }
.topbar-bc a:hover { color: var(--text); }
.topbar-bc .sep { margin: 0 3px; color: var(--border); }
.topbar-actions { display: flex; gap: 4px; flex-shrink: 0; }
.topbar-btn {
    background: none; border: none; color: var(--text2); cursor: pointer;
    padding: 4px 8px; border-radius: 4px; font-size: 13px;
    text-decoration: none; display: inline-flex; align-items: center;
}
.topbar-btn:hover { background: var(--surface); color: var(--text); }
.overlay {
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.3); z-index: 50;
}

/* === Mobile === */
@media (max-width: 768px) {
    .sidebar { transform: translateX(-100%); width: 85vw; max-width: 300px; }
    .sidebar.open { transform: translateX(0); box-shadow: 2px 0 12px rgba(0,0,0,0.15); }
    .overlay.open { display: block; }
    .main-wrapper { margin-left: 0; }
    .main { padding: 10px 14px 30px; max-width: 100vw; width: 100%; }
    .cover { max-height: 140px; }
    .cover img { height: 140px; }
    .page-icon { font-size: 56px; line-height: 56px; margin-top: -30px; }
    .page-icon .lucide-icon { width: 52px; height: 52px; }
    .md h1 { font-size: 1.5em; }
    .md h2 { font-size: 1.25em; }
    .md pre { padding: 8px; font-size: 12px; }
    .gallery { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
    .card-cover { height: 80px; }
    .card-body { padding: 8px 10px; }
    .card-title { font-size: 13px; }
    .db-table { font-size: 12px; }
    .db-row { padding: 6px 10px; font-size: 13px; }
    .breadcrumb { font-size: 11px; }
}
"""

JS = """
// Tree toggle
document.addEventListener('click', function(e) {
    const item = e.target.closest('.tree-dir > .tree-item');
    if (item) { e.preventDefault(); item.parentElement.classList.toggle('open'); }
});

// Sidebar toggle
const menuBtn = document.getElementById('sidebar-toggle');
const sidebar = document.querySelector('.sidebar');
const overlay = document.querySelector('.overlay');
const isMobile = () => window.innerWidth <= 768;
function toggleMenu(open) {
    if (isMobile()) {
        const o = open !== undefined ? open : !sidebar.classList.contains('open');
        sidebar.classList.toggle('open', o);
        overlay.classList.toggle('open', o);
        document.body.style.overflow = o ? 'hidden' : '';
    } else {
        const hidden = sidebar.classList.toggle('hidden');
        document.querySelector('.main-wrapper').style.marginLeft = hidden ? '0' : '';
    }
}
if (menuBtn) {
    menuBtn.addEventListener('click', () => toggleMenu());
    overlay.addEventListener('click', () => toggleMenu(false));
}

// Search
const si = document.getElementById('sidebar-search');
if (si) {
    let debounce;
    si.addEventListener('input', function() {
        clearTimeout(debounce);
        const q = this.value.trim();
        const items = document.querySelectorAll('.tree-file');
        const dirs = document.querySelectorAll('.tree-dir');
        if (!q) {
            items.forEach(i => i.style.display = '');
            dirs.forEach(d => { d.style.display = ''; d.classList.remove('open'); });
            document.querySelector('.search-results').innerHTML = '';
            return;
        }
        // Tree filter
        const ql = q.toLowerCase();
        items.forEach(i => {
            i.style.display = i.querySelector('.tree-item').textContent.toLowerCase().includes(ql) ? '' : 'none';
        });
        dirs.forEach(d => {
            const vis = d.querySelector('.tree-file:not([style*="display: none"])');
            d.style.display = vis ? '' : 'none';
            if (vis) d.classList.add('open');
        });
        // API search (debounced)
        if (q.length >= 2) {
            debounce = setTimeout(() => {
                fetch('/api/search?q=' + encodeURIComponent(q))
                    .then(r => r.json())
                    .then(results => {
                        const c = document.querySelector('.search-results');
                        if (!results.length) { c.innerHTML = '<div style="padding:6px 8px;color:var(--text2);font-size:12px;">Nothing found</div>'; return; }
                        c.innerHTML = results.slice(0, 15).map(r =>
                            '<a class="sr-item" href="/view/' + encodeURIComponent(r.path) + '">' +
                            '<div>' + r.name + '</div>' +
                            '<div class="sr-path">' + r.path + '</div>' +
                            (r.match ? '<div class="sr-match">...' + r.match + '...</div>' : '') + '</a>'
                        ).join('');
                    });
            }, 200);
        }
    });
}

// Tab in textarea
const ea = document.querySelector('.edit-area');
if (ea) {
    ea.addEventListener('keydown', function(e) {
        if (e.key === 'Tab') {
            e.preventDefault();
            const s = this.selectionStart, end = this.selectionEnd;
            this.value = this.value.substring(0, s) + '    ' + this.value.substring(end);
            this.selectionStart = this.selectionEnd = s + 4;
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); this.closest('form').submit(); }
    });
}

// Close sidebar on nav (mobile)
document.querySelectorAll('.sidebar a').forEach(a => {
    a.addEventListener('click', () => { if (window.innerWidth <= 768) toggleMenu(false); });
});

// Copy buttons on code blocks
document.querySelectorAll('pre > code').forEach(function(block) {
    const pre = block.parentNode;
    // Wrap pre in a container for sticky button
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.style.cssText = 'position:absolute;top:6px;right:6px;padding:2px 8px;font-size:11px;background:var(--surface);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--text2);opacity:0;transition:opacity 0.15s;z-index:1;';
    wrap.appendChild(btn);
    wrap.addEventListener('mouseenter', () => btn.style.opacity = '1');
    wrap.addEventListener('mouseleave', () => btn.style.opacity = '0');
    btn.addEventListener('click', () => {
        navigator.clipboard.writeText(block.textContent).then(() => {
            btn.textContent = 'Copied!';
            setTimeout(() => btn.textContent = 'Copy', 1500);
        });
    });
});

// Initialize Lucide icons
if (typeof lucide !== 'undefined') lucide.createIcons();

// KaTeX auto-render
document.addEventListener('DOMContentLoaded', function() {
    if (typeof renderMathInElement !== 'undefined') {
        renderMathInElement(document.body, {
            delimiters: [
                {left: '$$', right: '$$', display: true},
                {left: '$', right: '$', display: false}
            ],
            throwOnError: false
        });
    }
});
"""


# --- HTML helpers ---

def build_tree_html(items: list[dict], depth: int = 0, current_path: str = "") -> str:
    html = ""
    for item in items:
        style = f"--depth:{depth}"
        if item["type"] == "dir":
            is_open = current_path.startswith(item["path"])
            cls = "tree-dir open" if is_open else "tree-dir"
            dir_icon = get_icon_html(item["path"], "&#128193;")
            html += f'<div class="{cls}">'
            html += f'<div class="tree-item" style="{style}"><span class="chv">&#9654;</span>{dir_icon}{item["name"]}</div>'
            html += f'<div class="tree-children">{build_tree_html(item["children"], depth+1, current_path)}</div></div>'
        else:
            active = "active" if item["path"] == current_path else ""
            fallback = "&#127912;" if item["name"].endswith(".canvas") else "&#128196;"
            file_icon = get_icon_html(item["path"], fallback)
            html += f'<div class="tree-file"><a class="tree-item {active}" href="/view/{item["path"]}" style="{style}">{file_icon}{item["name"]}</a></div>'
    return html


def layout(title: str, content: str, current_path: str = "", toast: str = "") -> HTMLResponse:
    tree = get_file_tree(VAULT_ROOT)
    tree_html = build_tree_html(tree, current_path=current_path)

    bc_inner = f'<a href="/">{APP_TITLE}</a>'
    edit_actions = ""
    if current_path:
        parts = current_path.split("/")
        crumbs = [f'<a href="/">{APP_TITLE}</a>']
        for i, part in enumerate(parts):
            p = "/".join(parts[:i + 1])
            if i == len(parts) - 1:
                crumbs.append(f"<span>{part}</span>")
            else:
                crumbs.append(f'<a href="/view/{p}">{part}</a>')
        bc_inner = '<span class="sep">/</span>'.join(crumbs)
        # Add edit/raw buttons for files
        if not Path(current_path).suffix == "" and current_path:
            ext = Path(current_path).suffix.lower()
            if ext in (".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".base"):
                edit_actions = f'<a class="topbar-btn" href="/edit/{current_path}" title="Edit"><i data-lucide="pencil" style="width:14px;height:14px"></i></a><a class="topbar-btn" href="/raw/{current_path}" title="Raw"><i data-lucide="file-code" style="width:14px;height:14px"></i></a>'

    toast_html = f'<div class="toast">{toast}</div>' if toast else ""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{title} — {APP_TITLE}</title>
<style>{CSS}</style>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
</head>
<body>
<div class="overlay"></div>
<nav class="sidebar">
    <div class="sidebar-hdr"><a href="/">&#128218; {APP_TITLE}</a></div>
    <div class="sidebar-search"><input type="text" id="sidebar-search" placeholder="Search..." autocomplete="off"></div>
    <div class="search-results"></div>
    <div class="tree">{tree_html}</div>
</nav>
<div class="main-wrapper">
<main class="main">
    <div class="topbar">
        <button class="topbar-toggle" id="sidebar-toggle" title="Toggle sidebar"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="2" width="22" height="20" rx="4"></rect><rect x="4" y="5" width="2" height="14" rx="2" fill="currentColor"></rect></svg></button>
        <div class="topbar-bc">{bc_inner}</div>
        <div class="topbar-actions">{edit_actions}</div>
    </div>
    {content}
</main>
</div>
{toast_html}
<script>{JS}</script>
</body>
</html>""")


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def index():
    total = sum(1 for _ in VAULT_ROOT.rglob("*.md"))
    recent = sorted(
        [f for f in VAULT_ROOT.rglob("*.md") if not f.name.startswith(".")],
        key=lambda f: f.stat().st_mtime, reverse=True
    )[:15]
    recent_html = "".join(
        f'<a class="sr-item" style="padding:8px 0" href="/view/{f.relative_to(VAULT_ROOT)}">'
        f'<div style="font-weight:500">{f.stem}</div>'
        f'<div class="sr-path">{f.relative_to(VAULT_ROOT)}</div></a>'
        for f in recent
    )

    # Quick links to base views
    dirs_with_md = []
    for d in sorted(VAULT_ROOT.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_"):
            md_count = sum(1 for _ in d.glob("*.md"))
            if md_count > 0:
                dirs_with_md.append((d.name, md_count))

    bases_html = "".join(
        f'<a class="btn" href="/base/{name}" style="margin-right:4px;margin-bottom:4px">'
        f'&#128193; {name} <span style="color:var(--text2);font-size:11px">({count})</span></a>'
        for name, count in dirs_with_md
    )

    content = f"""
    <h1 style="font-size:28px;font-weight:700;margin-bottom:6px;">&#128218; Vault</h1>
    <p style="color:var(--text2);margin-bottom:20px">{total} notes</p>
    <div style="margin-bottom:24px">{bases_html}</div>
    <div class="home-recent"><h3>Recently modified</h3>{recent_html}</div>
    """
    return layout(APP_TITLE, content)


def render_base_view(fp: Path, file_path: str, active_tab: int = 0) -> HTMLResponse:
    """Render an Obsidian .base file with tabs and filtered views."""
    base = parse_base_file(fp)
    global_filters = base.get("filters", {})
    views = base.get("views", [])

    if not views:
        return layout(fp.stem, '<p style="color:var(--text2)">Empty base file</p>', file_path)

    active_tab = min(active_tab, len(views) - 1)

    # Tabs
    tabs_html = '<div class="view-tabs" style="margin-bottom:16px">'
    for i, v in enumerate(views):
        name = v.get("name", f"View {i+1}")
        active = "active" if i == active_tab else ""
        tabs_html += f'<a class="view-tab {active}" href="/view/{file_path}?tab={i}">{_escape(name)}</a>'
    tabs_html += '</div>'

    # Active view
    view = views[active_tab]
    view_type = view.get("type", "cards")
    view_filters = view.get("filters", {})
    image_field = view.get("image", "")
    aspect = view.get("imageAspectRatio", 0.5)
    columns = view.get("order", [])

    entries = collect_base_entries(global_filters, view_filters)

    # Sort
    sort_rules = view.get("sort", [])
    for rule in reversed(sort_rules):
        prop = rule.get("property", "").replace("file.", "").replace("note.", "")
        desc = rule.get("direction", "ASC").upper() == "DESC"
        if prop == "name":
            entries.sort(key=lambda e: e["name"].lower(), reverse=desc)
        elif prop:
            entries.sort(key=lambda e: str(e["meta"].get(prop, "")).lower(), reverse=desc)

    title = fp.stem
    header = f'<h2 style="font-size:22px;font-weight:700;margin-bottom:4px">{_escape(title)}</h2>'
    info = f'<div class="filter-info" style="margin-bottom:12px">{len(entries)} items</div>'

    if view_type == "cards":
        body = render_base_cards(entries, image_field, aspect)
    else:
        body = render_base_table(entries, columns)

    content = header + tabs_html + info + body
    return layout(f"{title} — Base", content, file_path)


@app.get("/base/{dir_path:path}", response_class=HTMLResponse)
async def base_view(dir_path: str, view: str = Query("cards")):
    """Database-like view of a directory: cards, list, or table."""
    dp = safe_path(dir_path)
    if not dp.is_dir():
        raise HTTPException(404, "Not a directory")

    # Collect all md files with their metadata
    entries = []
    for fp in sorted(dp.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True):
        if fp.name.startswith("."):
            continue
        meta = parse_meta(fp)
        cover = ""
        for f in COVER_FIELDS:
            if f in meta and meta[f]:
                cover = resolve_img(str(meta[f]))
                if cover:
                    break
        status = meta.get("status", [])
        if isinstance(status, str):
            status = [status]
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        entries.append({
            "name": fp.stem,
            "path": str(fp.relative_to(VAULT_ROOT)),
            "cover": cover,
            "status": status if isinstance(status, list) else [],
            "tags": tags if isinstance(tags, list) else [],
            "meta": meta,
            "mtime": fp.stat().st_mtime,
        })

    # View tabs
    tabs = ""
    for v, label, icon in [("cards", "Cards", "&#9638;"), ("list", "List", "&#9776;"), ("table", "Table", "&#9637;")]:
        active = "active" if view == v else ""
        tabs += f'<a class="view-tab {active}" href="/base/{dir_path}?view={v}">{icon} {label}</a>'

    toolbar = f"""
    <div class="db-toolbar">
        <h2 style="font-size:22px;font-weight:700">{dp.name}</h2>
        <div class="view-tabs">{tabs}</div>
    </div>
    <div class="filter-info" style="margin-bottom:12px">{len(entries)} items</div>
    """

    if view == "cards":
        cards = ""
        for e in entries:
            cover_html = ""
            if e["cover"]:
                cover_html = f'<div class="card-cover"><img src="{e["cover"]}" loading="lazy"></div>'
            else:
                cover_html = '<div class="card-cover" style="background:var(--bg2);display:flex;align-items:center;justify-content:center;color:var(--text2);font-size:28px;">&#128196;</div>'

            badges_html = ""
            for s in e["status"][:2]:
                c = status_color(str(s))
                badges_html += f'<span class="badge badge-{c}">{s}</span>'
            for t in e["tags"][:3]:
                badges_html += f'<span class="tag">{t}</span>'

            cards += f"""
            <div class="card"><a href="/view/{e['path']}">
                {cover_html}
                <div class="card-body">
                    <div class="card-title">{_escape(e['name'])}</div>
                    <div class="card-meta">{badges_html}</div>
                </div>
            </a></div>"""
        body = f'<div class="gallery">{cards}</div>'

    elif view == "list":
        rows = ""
        for e in entries:
            status_html = ""
            for s in e["status"][:1]:
                c = status_color(str(s))
                status_html += f'<span class="badge badge-{c}">{s}</span>'
            tags_html = "".join(f'<span class="tag">{t}</span>' for t in e["tags"][:3])
            rows += f"""
            <a class="db-row" href="/view/{e['path']}">
                <div class="db-row-title">{_escape(e['name'])}</div>
                <div class="db-row-status">{status_html}</div>
                <div class="db-row-tags">{tags_html}</div>
            </a>"""
        body = f'<div class="db-list">{rows}</div>'

    else:  # table
        # Collect all unique property keys
        all_keys = set()
        for e in entries:
            all_keys.update(e["meta"].keys())
        # Standard columns
        cols = ["status", "tags"]
        extra = sorted(k for k in all_keys if k.lower() not in (COVER_FIELDS | BADGE_FIELDS | SKIP_FIELDS | {"status", "tags"}) and any(str(ee["meta"].get(k, "")).strip() for ee in entries))
        cols.extend(extra[:5])

        ths = '<th>Name</th>' + "".join(f'<th>{_escape(c)}</th>' for c in cols)
        trs = ""
        for e in entries:
            tds = f'<td><a href="/view/{e["path"]}">{_escape(e["name"])}</a></td>'
            for c in cols:
                val = e["meta"].get(c, "")
                if c == "status":
                    cell = "".join(f'<span class="badge badge-{status_color(str(s))}">{s}</span>' for s in e["status"])
                elif c in ("tags", "tag", "labels"):
                    cell = '<div class="cell-tags">' + "".join(f'<span class="tag">{t}</span>' for t in e["tags"]) + '</div>'
                else:
                    cell = _escape(str(val))[:100]
                tds += f'<td>{cell}</td>'
            trs += f'<tr>{tds}</tr>'
        body = f'<div class="table-wrap"><table class="db-table"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>'

    content = toolbar + body
    return layout(f"{dp.name} — Base", content, dir_path)


@app.get("/view/{file_path:path}", response_class=HTMLResponse)
async def view_file(file_path: str, toast: str = "", tab: int = 0):
    fp = safe_path(file_path)
    if fp.is_dir():
        return RedirectResponse(f"/base/{file_path}?view=cards")
    if not fp.exists():
        raise HTTPException(404, "File not found")

    # Handle .base files
    if fp.suffix == ".base":
        return render_base_view(fp, file_path, tab)

    # Non-text files → serve directly
    TEXT_EXTS = {".md", ".txt", ".canvas", ".csv", ".json", ".yaml", ".yml"}
    if fp.suffix.lower() not in TEXT_EXTS:
        mime, _ = mimetypes.guess_type(str(fp))
        return Response(content=fp.read_bytes(), media_type=mime or "application/octet-stream")

    raw = fp.read_text(encoding="utf-8", errors="replace")
    post = frontmatter.loads(raw)
    parts = get_page_parts(post.metadata, file_path)

    title = fp.stem
    has_cover = bool(parts["cover"])
    no_cover_cls = "" if has_cover else "no-cover"

    # Notion4ever layout: cover → icon (overlapping) → title → badges → props → content
    page_title = f'<h1 style="font-size:2em;font-weight:700;margin:0 0 0.3em;font-family:var(--font)">{_escape(title)}</h1>'

    md_html = render_md(post.content)

    content = (
        f'{parts["cover"]}'
        f'<div class="{no_cover_cls}">'
        f'{parts["icon"]}'
        f'{page_title}'
        f'{parts["badges"]}'
        f'{parts["props"]}'
        f'</div>'
        f'<div class="md">{md_html}</div>'
    )
    return layout(fp.name, content, file_path, toast=toast)


@app.get("/edit/{file_path:path}", response_class=HTMLResponse)
async def edit_file(file_path: str):
    fp = safe_path(file_path)
    if not fp.exists():
        raise HTTPException(404, "File not found")
    raw = fp.read_text(encoding="utf-8", errors="replace")
    content = f"""
    <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <button class="btn" style="color:var(--red)" onclick="if(this.dataset.armed){{document.getElementById('df').submit()}}else{{this.textContent='Confirm delete';this.dataset.armed='1'}}" title="Delete">&#128465; Delete</button>
        <button class="btn btn-primary" type="submit" form="ef">&#128190; Save</button>
    </div>
    <form id="ef" method="POST" action="/save/{file_path}">
        <textarea class="edit-area" name="content">{_escape(raw)}</textarea>
    </form>
    <form id="df" method="POST" action="/delete/{file_path}" style="display:none"></form>"""
    return layout(f"Edit: {fp.name}", content, file_path)


@app.post("/save/{file_path:path}")
async def save_file(file_path: str, content: str = Form(...)):
    fp = safe_path(file_path)
    if not fp.exists():
        raise HTTPException(404, "File not found")
    fp.write_text(content, encoding="utf-8")
    return RedirectResponse(f"/view/{file_path}?toast=Saved", status_code=303)


@app.post("/delete/{file_path:path}")
async def delete_file(file_path: str):
    fp = safe_path(file_path)
    if not fp.exists():
        raise HTTPException(404, "File not found")
    parent = str(fp.parent.relative_to(VAULT_ROOT))
    fp.unlink()
    return RedirectResponse(f"/view/{parent}?toast=Deleted", status_code=303)


@app.get("/raw/{file_path:path}")
async def raw_file(file_path: str):
    fp = safe_path(file_path)
    if not fp.exists():
        raise HTTPException(404, "File not found")
    return Response(content=fp.read_text(encoding="utf-8", errors="replace"),
                    media_type="text/plain; charset=utf-8")


@app.get("/static/{file_path:path}")
async def static_file(file_path: str):
    fp = safe_path(file_path)
    if not fp.exists() or fp.is_dir():
        raise HTTPException(404, "Not found")
    mime, _ = mimetypes.guess_type(str(fp))
    return Response(content=fp.read_bytes(), media_type=mime or "application/octet-stream")


@app.get("/api/search")
async def search_api(q: str = ""):
    if len(q) < 2:
        return JSONResponse([])
    results = []
    ql = q.lower()
    for fp in VAULT_ROOT.rglob("*"):
        if fp.name.startswith(".") or fp.is_dir() or fp.suffix.lower() not in (".md", ".txt", ".canvas", ".yaml", ".yml"):
            continue
        rel = str(fp.relative_to(VAULT_ROOT))
        if ql in fp.name.lower():
            results.append({"name": fp.name, "path": rel, "match": "", "score": 2})
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
            idx = text.lower().find(ql)
            if idx >= 0:
                s = max(0, idx - 40)
                snippet = text[s:idx + len(q) + 40].replace("\n", " ")
                results.append({"name": fp.name, "path": rel, "match": snippet, "score": 1})
        except Exception:
            continue
    results.sort(key=lambda r: -r["score"])
    return JSONResponse([{"name": r["name"], "path": r["path"], "match": r["match"]} for r in results[:30]])


def _apply_config(strict: bool = False):
    """Resolve VAULT_ROOT / APP_TITLE after CLI args are parsed."""
    global VAULT_ROOT, HOST, PORT, APP_TITLE
    VAULT_ROOT = VAULT_ROOT.resolve()
    if not VAULT_ROOT.is_dir():
        msg = f"Error: vault directory '{VAULT_ROOT}' does not exist."
        if strict:
            print(msg, file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Warning: {msg} Create it or set VAULT_ROOT.", file=sys.stderr)
    if not APP_TITLE:
        APP_TITLE = VAULT_ROOT.name
    app.title = APP_TITLE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Obsidian Vault Viewer")
    parser.add_argument("--vault", type=str, default=None, help="Path to Obsidian vault (overrides VAULT_ROOT env)")
    parser.add_argument("--host", type=str, default=None, help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8000)")
    parser.add_argument("--title", type=str, default=None, help="App title (default: vault folder name)")
    args = parser.parse_args()

    if args.vault:
        VAULT_ROOT = Path(args.vault)
    if args.host:
        HOST = args.host
    if args.port:
        PORT = args.port
    if args.title:
        APP_TITLE = args.title

    _apply_config(strict=True)

    import uvicorn
    print(f"Serving vault: {VAULT_ROOT} on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
else:
    # When imported (e.g. uvicorn app:app), resolve config from env vars
    _apply_config()
