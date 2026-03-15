"""
Silmaril — self-hosted, mobile-first web UI for Obsidian vaults.
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

# Extended config (loaded from silmaril.yml or vault-viewer.yml)
CONFIG = {
    "favicon": "",          # URL or path to favicon
    "custom_css": "",       # Extra CSS injected into every page
    "custom_head": "",      # Extra HTML injected into <head>
    "hide": [],             # Glob patterns to hide from tree (e.g. ["_private/**", "*.tmp"])
    "pinch_zoom": True,     # Allow pinch-to-zoom on mobile
    "readonly": False,      # Disable edit/delete
}

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


def get_raw_icon(rel_path: str) -> str:
    """Get raw icon value (emoji or lucide-name) for a vault path."""
    icons = load_icons()
    return icons.get(rel_path, {}).get("icon", "")


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


def _save_icon(rel_path: str, icon: str, color: str = "", is_folder: bool = False):
    """Save icon to Iconic plugin data.json."""
    global _icon_cache
    iconic_path = VAULT_ROOT / ".obsidian" / "plugins" / "iconic" / "data.json"
    iconic_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(iconic_path.read_text(encoding="utf-8")) if iconic_path.exists() else {}
    except Exception:
        data = {}
    section = "folderIcons" if is_folder else "fileIcons"
    if section not in data:
        data[section] = {}
    entry = {"icon": icon}
    if color:
        entry["color"] = color
    data[section][rel_path] = entry
    iconic_path.write_text(json.dumps(data, indent="\t", ensure_ascii=False), encoding="utf-8")
    _icon_cache = None


def _remove_icon(rel_path: str, is_folder: bool = False):
    """Remove icon from Iconic plugin data.json."""
    global _icon_cache
    iconic_path = VAULT_ROOT / ".obsidian" / "plugins" / "iconic" / "data.json"
    try:
        data = json.loads(iconic_path.read_text(encoding="utf-8"))
    except Exception:
        return
    section = "folderIcons" if is_folder else "fileIcons"
    if section in data and rel_path in data[section]:
        del data[section][rel_path]
        iconic_path.write_text(json.dumps(data, indent="\t", ensure_ascii=False), encoding="utf-8")
    _icon_cache = None


# --- File tree ---

def _is_hidden(rel: str) -> bool:
    """Check if path matches any hide pattern from config."""
    import fnmatch
    for pattern in CONFIG.get("hide", []):
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern):
            return True
    return False


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
        if _is_hidden(rel):
            continue
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
        href = f"/{target}" if target.endswith(".md") else f"/{target}.md"
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
        _wikilink_cache[target] = f"/{t}"
        return _wikilink_cache[target]
    # Search vault by filename
    name = Path(t).name
    for fp in VAULT_ROOT.rglob(name):
        rel = str(fp.relative_to(VAULT_ROOT))
        _wikilink_cache[target] = f"/{rel}"
        return _wikilink_cache[target]
    # Not found — link anyway
    _wikilink_cache[target] = f"/{t}"
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
    # Convert checkboxes before markdown so they survive any wrapping
    content = re.sub(r'^(\s*[-*]\s*)\[ \]', r'\1⟨CB:unchecked⟩', content, flags=re.MULTILINE)
    content = re.sub(r'^(\s*[-*]\s*)\[[xX]\]', r'\1⟨CB:checked⟩', content, flags=re.MULTILINE)
    html = markdown.markdown(content, extensions=[
        'tables', 'fenced_code', 'codehilite', 'toc', 'nl2br', 'sane_lists', 'smarty'
    ])
    html = re.sub(r'⟨TAG:(.+?)⟩', r'<span class="tag">#\1</span>', html)
    html = _restore_math(html)
    html = html.replace("<table", '<div class="table-wrap"><table').replace("</table>", "</table></div>")
    # Restore checkboxes and add task-item class to parent <li>
    html = html.replace("⟨CB:unchecked⟩", '<input type="checkbox">')
    html = html.replace("⟨CB:checked⟩", '<input type="checkbox" checked>')
    html = re.sub(r'<li>(\s*(?:<p>)?\s*<input type="checkbox")', r'<li class="task-item">\1', html)
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

    # Icon from Iconic plugin (independent of frontmatter)
    if file_path and get_raw_icon(file_path):
        icon_html = get_icon_html(file_path, "")
        result["icon"] = f'<div class="page-icon" data-icon-path="{file_path}">{icon_html}</div>'
    elif file_path and not CONFIG.get("readonly"):
        result["icon"] = f'<div class="page-icon page-icon-add" data-icon-path="{file_path}"><span class="icon-add-btn">+</span></div>'

    if not meta:
        return result

    # Cover
    for f in COVER_FIELDS:
        if f in meta and meta[f]:
            url = resolve_img(str(meta[f]))
            if url:
                result["cover"] = f'<div class="cover"><img src="{url}" alt="" loading="lazy"></div>'
                break

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
        cards += f'<div class="card"><a href="/{e["path"]}">{cover_html}<div class="card-body"><div class="card-title">{card_icon}{_escape(e["name"])}</div><div class="card-meta">{badges_html}</div></div></a></div>'
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
        tds = f'<td><a href="/{e["path"]}">{_escape(e["name"])}</a></td>'
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


# --- Canvas renderer ---

import json

CANVAS_COLORS = {
    "1": "#fb464c", "2": "#e9973f", "3": "#e0de71",
    "4": "#44cf6e", "5": "#53dfdd", "6": "#a882ff",
}


def render_canvas_view(fp: Path, file_path: str) -> HTMLResponse:
    """Render an Obsidian .canvas file as a read-only visual board."""
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return layout(fp.stem, '<p>Failed to parse canvas</p>', file_path)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    if not nodes:
        return layout(fp.stem, '<p>Empty canvas</p>', file_path)

    # Calculate bounds for viewBox
    min_x = min(n.get("x", 0) for n in nodes)
    min_y = min(n.get("y", 0) for n in nodes)
    max_x = max(n.get("x", 0) + n.get("width", 250) for n in nodes)
    max_y = max(n.get("y", 0) + n.get("height", 60) for n in nodes)

    pad = 40
    vw = max_x - min_x + pad * 2
    vh = max_y - min_y + pad * 2

    # Build node lookup for edges
    node_map = {}
    for n in nodes:
        nid = n.get("id", "")
        node_map[nid] = {
            "cx": n.get("x", 0) + n.get("width", 250) / 2,
            "cy": n.get("y", 0) + n.get("height", 60) / 2,
        }

    # Render edges as SVG lines
    edge_lines = ""
    for e in edges:
        fn = node_map.get(e.get("fromNode", ""), {})
        tn = node_map.get(e.get("toNode", ""), {})
        if fn and tn:
            edge_lines += f'<line x1="{fn["cx"]}" y1="{fn["cy"]}" x2="{tn["cx"]}" y2="{tn["cy"]}" stroke="var(--border)" stroke-width="2"/>'

    # Render nodes as foreignObject with HTML inside
    node_els = ""
    for n in nodes:
        x, y = n.get("x", 0), n.get("y", 0)
        w, h = n.get("width", 250), n.get("height", 60)
        color = CANVAS_COLORS.get(n.get("color", ""), "")
        border_style = f"border-left: 3px solid {color};" if color else ""
        ntype = n.get("type", "text")

        if ntype == "text":
            text = n.get("text", "")
            inner = render_md(text)
        elif ntype == "file":
            fpath = n.get("file", "")
            fname = Path(fpath).stem
            inner = f'<a href="/{fpath}" class="wikilink" style="font-weight:500">{_escape(fname)}</a>'
        elif ntype == "link":
            url = n.get("url", "")
            inner = f'<a href="{url}" target="_blank" style="word-break:break-all">{_escape(url)}</a>'
        else:
            inner = _escape(str(n))

        node_els += (
            f'<foreignObject x="{x}" y="{y}" width="{w}" height="{h}">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="canvas-node" style="{border_style}">'
            f'{inner}</div></foreignObject>'
        )

    canvas_html = f"""
    <div class="canvas-container">
        <svg viewBox="{min_x - pad} {min_y - pad} {vw} {vh}" xmlns="http://www.w3.org/2000/svg">
            {edge_lines}
            {node_els}
        </svg>
    </div>
    """

    title = fp.stem
    content = f'<h2 style="font-weight:700;margin-bottom:12px">{_escape(title)}</h2>{canvas_html}'
    return layout(f"{title} — Canvas", content, file_path)


# --- CSS & JS (loaded from static files) ---

CSS = (Path(__file__).parent / "static" / "style.css").read_text()
JS = (Path(__file__).parent / "static" / "script.js").read_text()



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
            html += f'<div class="tree-file"><a class="tree-item {active}" href="/{item["path"]}" style="{style}">{file_icon}{item["name"]}</a></div>'
    return html


def layout(title: str, content: str, current_path: str = "", toast: str = "", page_icon: str = "") -> HTMLResponse:
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
                crumbs.append(f'<a href="/{p}">{part}</a>')
        bc_inner = '<span class="sep">/</span>'.join(crumbs)
        # Add edit/raw buttons for files
        if not Path(current_path).suffix == "" and current_path:
            ext = Path(current_path).suffix.lower()
            if ext in (".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".base"):
                raw_btn = f'<a class="topbar-btn" href="/{current_path}?raw" title="Raw"><i data-lucide="file-code" style="width:14px;height:14px"></i></a>'
                if CONFIG.get("readonly"):
                    edit_actions = raw_btn
                else:
                    edit_actions = f'<a class="topbar-btn" href="/{current_path}?edit" title="Edit"><i data-lucide="pencil" style="width:14px;height:14px"></i></a>{raw_btn}'

    toast_html = f'<div class="toast">{toast}</div>' if toast else ""

    icon_picker_html = ""
    if not CONFIG.get("readonly"):
        icon_picker_html = """<div id="icon-picker-overlay"></div>
<div id="icon-picker">
<div class="icon-picker-header">
<span class="icon-picker-title">Choose icon</span>
<button class="icon-picker-close" id="icon-picker-close">&times;</button>
</div>
<div class="icon-picker-tabs">
<button class="icon-picker-tab active" data-tab="emoji">Emoji</button>
<button class="icon-picker-tab" data-tab="lucide">Lucide</button>
</div>
<div class="icon-picker-search-wrap"><input type="text" id="icon-picker-search" placeholder="Search icons..."></div>
<div class="icon-picker-custom"><input type="text" id="icon-picker-custom" placeholder="Paste custom emoji..."><button id="icon-picker-custom-btn">Use</button></div>
<div id="icon-picker-grid"></div>
<div class="icon-picker-footer">
<div class="icon-picker-color"><label>Color:</label><input type="color" id="icon-picker-color" value="#000000"><button id="icon-picker-color-reset">Reset</button></div>
<button class="btn" id="icon-picker-remove" style="color:var(--red)">&#128465; Remove</button>
</div>
</div>"""

    viewport = "width=device-width, initial-scale=1.0" if CONFIG.get("pinch_zoom", True) else "width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"
    # Favicon: page icon (emoji) > config favicon > default
    favicon_html = ""
    if page_icon and not page_icon.startswith("lucide-"):
        # Emoji → SVG data URI (notion4ever trick)
        emoji = page_icon
        favicon_html = f'<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>{emoji}</text></svg>">'
    elif CONFIG.get("favicon"):
        favicon_html = f'<link rel="icon" href="{CONFIG["favicon"]}">'
    else:
        favicon_html = '<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📄</text></svg>">'
    custom_css = f'<style>{CONFIG["custom_css"]}</style>' if CONFIG.get("custom_css") else ""
    custom_head = CONFIG.get("custom_head", "")

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="{viewport}">
<title>{title} — {APP_TITLE}</title>
{favicon_html}
<style>{CSS}</style>
{custom_css}
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
{custom_head}
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
{icon_picker_html}
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
        f'<a class="sr-item" style="padding:8px 0" href="/{f.relative_to(VAULT_ROOT)}">'
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
        tabs_html += f'<a class="view-tab {active}" href="/{file_path}?tab={i}">{_escape(name)}</a>'
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
            <div class="card"><a href="/{e['path']}">
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
            <a class="db-row" href="/{e['path']}">
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
            tds = f'<td><a href="/{e["path"]}">{_escape(e["name"])}</a></td>'
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


async def _render_file(file_path: str, toast: str = "", tab: int = 0,
                       edit: str = None, raw: str = None):
    fp = safe_path(file_path)
    if fp.is_dir():
        return RedirectResponse(f"/base/{file_path}?view=cards")
    if not fp.exists():
        raise HTTPException(404, "File not found")

    # Handle .base files
    if fp.suffix == ".base":
        return render_base_view(fp, file_path, tab)

    # Handle .canvas files
    if fp.suffix == ".canvas":
        return render_canvas_view(fp, file_path)

    # Non-text files → serve directly
    TEXT_EXTS = {".md", ".txt", ".canvas", ".csv", ".json", ".yaml", ".yml"}
    if fp.suffix.lower() not in TEXT_EXTS:
        mime, _ = mimetypes.guess_type(str(fp))
        return Response(content=fp.read_bytes(), media_type=mime or "application/octet-stream")

    raw_text = fp.read_text(encoding="utf-8", errors="replace")

    # ?raw → plain text
    if raw is not None:
        return Response(content=raw_text, media_type="text/plain; charset=utf-8")

    # ?edit → editor
    if edit is not None and not CONFIG.get("readonly"):
        content = f"""
        <div style="display:flex;justify-content:space-between;margin-bottom:8px">
            <button class="btn" style="color:var(--red)" onclick="if(this.dataset.armed){{document.getElementById('df').submit()}}else{{this.textContent='Confirm delete';this.dataset.armed='1'}}" title="Delete">&#128465; Delete</button>
            <button class="btn btn-primary" type="submit" form="ef">&#128190; Save</button>
        </div>
        <form id="ef" method="POST" action="/save/{file_path}">
            <textarea class="edit-area" name="content">{_escape(raw_text)}</textarea>
        </form>
        <form id="df" method="POST" action="/delete/{file_path}" style="display:none"></form>"""
        return layout(f"Edit: {fp.name}", content, file_path)

    # Default: view
    post = frontmatter.loads(raw_text)
    parts = get_page_parts(post.metadata, file_path)

    title = fp.stem
    has_cover = bool(parts["cover"])
    has_icon = bool(get_raw_icon(file_path))
    cls = []
    if not has_cover:
        cls.append("no-cover")
    if has_icon:
        cls.append("has-icon")
    wrapper_cls = " ".join(cls)

    page_title = f'<h1 style="font-size:2em;font-weight:700;margin:0 0 0.3em;font-family:var(--font)">{_escape(title)}</h1>'

    md_html = render_md(post.content)

    content = (
        f'{parts["cover"]}'
        f'<div class="{wrapper_cls}">'
        f'{parts["icon"]}'
        f'{page_title}'
        f'{parts["badges"]}'
        f'{parts["props"]}'
        f'</div>'
        f'<div class="md">{md_html}</div>'
    )
    page_icon = get_raw_icon(file_path)
    return layout(fp.name, content, file_path, toast=toast, page_icon=page_icon)


@app.post("/save/{file_path:path}")
async def save_file(file_path: str, content: str = Form(...)):
    fp = safe_path(file_path)
    if not fp.exists():
        raise HTTPException(404, "File not found")
    fp.write_text(content, encoding="utf-8")
    return RedirectResponse(f"/{file_path}?toast=Saved", status_code=303)


@app.post("/delete/{file_path:path}")
async def delete_file(file_path: str):
    fp = safe_path(file_path)
    if not fp.exists():
        raise HTTPException(404, "File not found")
    parent = str(fp.parent.relative_to(VAULT_ROOT))
    fp.unlink()
    return RedirectResponse(f"/{parent}?toast=Deleted", status_code=303)


@app.get("/static/{file_path:path}")
async def static_file(file_path: str):
    fp = safe_path(file_path)
    if not fp.exists() or fp.is_dir():
        raise HTTPException(404, "Not found")
    mime, _ = mimetypes.guess_type(str(fp))
    return Response(content=fp.read_bytes(), media_type=mime or "application/octet-stream")


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/assets/{path:path}")
async def assets(path: str):
    fp = (STATIC_DIR / path).resolve()
    if not str(fp).startswith(str(STATIC_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
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


@app.post("/api/icon/{file_path:path}")
async def set_icon_api(file_path: str, request: Request):
    if CONFIG.get("readonly"):
        raise HTTPException(403, "Read-only mode")
    body = await request.json()
    icon = body.get("icon", "")
    color = body.get("color", "")
    if not icon:
        raise HTTPException(400, "Icon required")
    fp = safe_path(file_path)
    _save_icon(file_path, icon, color, is_folder=fp.is_dir())
    return JSONResponse({"ok": True})


@app.delete("/api/icon/{file_path:path}")
async def remove_icon_api(file_path: str):
    if CONFIG.get("readonly"):
        raise HTTPException(403, "Read-only mode")
    fp = safe_path(file_path)
    _remove_icon(file_path, is_folder=fp.is_dir())
    return JSONResponse({"ok": True})


# --- Catch-all: clean URLs (MUST be last route) ---

@app.get("/{file_path:path}", response_class=HTMLResponse)
async def clean_view(file_path: str, toast: str = "", tab: int = 0,
                     edit: str = None, raw: str = None):
    return await _render_file(file_path, toast, tab, edit, raw)


def _load_config_file() -> dict:
    """Load silmaril.yml / vault-viewer.yml from current directory."""
    for name in ("silmaril.yml", "silmaril.yaml", "vault-viewer.yml", "vault-viewer.yaml"):
        p = Path(name)
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def _apply_config(strict: bool = False):
    """Resolve VAULT_ROOT / APP_TITLE after CLI args are parsed.

    Priority: CLI args > config file > env vars > defaults.
    Config file values are applied only when the corresponding global
    still holds its env-var / default value (i.e. CLI did not override).
    """
    global VAULT_ROOT, HOST, PORT, APP_TITLE

    cfg = _load_config_file()

    # Env-var defaults (set at module level) act as the baseline.
    # Config-file values override env defaults but NOT CLI args.
    env_vault = Path(os.environ.get("VAULT_ROOT", "./vault"))
    env_host = os.environ.get("VAULT_HOST", "0.0.0.0")
    env_port = int(os.environ.get("VAULT_PORT", "8000"))
    env_title = os.environ.get("VAULT_NAME", "")

    if VAULT_ROOT == env_vault and "vault" in cfg:
        VAULT_ROOT = Path(cfg["vault"])
    if HOST == env_host and "host" in cfg:
        HOST = str(cfg["host"])
    if PORT == env_port and "port" in cfg:
        PORT = int(cfg["port"])
    if APP_TITLE == env_title and "title" in cfg:
        APP_TITLE = str(cfg["title"])

    # Extended config keys
    for key in CONFIG:
        if key in cfg:
            CONFIG[key] = cfg[key]

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


def main():
    """Entry point for the ``silmaril`` console script."""
    global VAULT_ROOT, HOST, PORT, APP_TITLE

    parser = argparse.ArgumentParser(description="Silmaril — Obsidian vault viewer")
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


# When imported (e.g. uvicorn silmaril:app), resolve config from env vars
_apply_config()
