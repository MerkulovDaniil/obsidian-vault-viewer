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
    "theme": "",            # Obsidian community theme name (e.g. "Things", "Dracula")
}

# --- Theme loading ---
import urllib.request

_THEMES_INDEX_URL = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/community-css-themes.json"
_theme_css_cache: str = ""


def _load_theme(name: str) -> str:
    """Fetch Obsidian community theme CSS by name. Cached after first load."""
    global _theme_css_cache
    if _theme_css_cache:
        return _theme_css_cache
    if not name:
        return ""
    # Cache dir
    cache_dir = Path.home() / ".cache" / "silmaril" / "themes"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{name.lower().replace(' ', '-')}.css"
    if cache_file.exists():
        _theme_css_cache = cache_file.read_text(encoding="utf-8")
        return _theme_css_cache
    # Fetch themes index
    try:
        with urllib.request.urlopen(_THEMES_INDEX_URL, timeout=10) as r:
            themes = json.loads(r.read())
    except Exception as e:
        print(f"Warning: could not fetch themes index: {e}")
        return ""
    # Find theme by name (case-insensitive)
    repo = None
    nl = name.lower()
    for t in themes:
        if t["name"].lower() == nl:
            repo = t["repo"]
            break
    if not repo:
        print(f"Warning: theme '{name}' not found in Obsidian community themes")
        return ""
    # Fetch theme.css or obsidian.css from repo
    for branch in ("main", "master"):
        for fname in ("theme.css", "obsidian.css"):
            url = f"https://raw.githubusercontent.com/{repo}/{branch}/{fname}"
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    css = r.read().decode("utf-8")
                cache_file.write_text(css, encoding="utf-8")
                _theme_css_cache = css
                print(f"Theme '{name}' loaded from {repo}/{fname} ({len(css)} bytes)")
                return css
            except Exception:
                continue
    print(f"Warning: could not fetch theme from {repo}")
    return ""


# --- Pretty Properties plugin support ---

_pretty_props_cache = None

def _load_pretty_props() -> dict:
    """Load Pretty Properties plugin config from vault."""
    global _pretty_props_cache
    if _pretty_props_cache is not None:
        return _pretty_props_cache
    pp_path = VAULT_ROOT / ".obsidian" / "plugins" / "pretty-properties" / "data.json"
    if not pp_path.exists():
        _pretty_props_cache = {}
        return _pretty_props_cache
    try:
        _pretty_props_cache = json.loads(pp_path.read_text(encoding="utf-8"))
    except Exception:
        _pretty_props_cache = {}
    return _pretty_props_cache


# Obsidian color names → CSS RGB values
_OBS_COLORS = {
    "red": "233, 49, 71", "orange": "236, 117, 0", "yellow": "224, 172, 0",
    "green": "8, 185, 78", "cyan": "0, 191, 188", "blue": "8, 109, 221",
    "purple": "120, 82, 238", "pink": "213, 57, 132",
}


def _pill_html(text: str, color_name: str = "") -> str:
    """Render a colored pill span using Obsidian color names."""
    rgb = _OBS_COLORS.get(color_name, "")
    if rgb:
        return f'<span class="pp-pill" style="--pp-rgb:{rgb}">{_escape(str(text))}</span>'
    return f'<span class="pp-pill">{_escape(str(text))}</span>'


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
    # Obsidian-native: lucide icon + RGB color per callout type
    CALLOUT_META = {
        "note":     ("pencil",          "8, 109, 221"),
        "abstract": ("clipboard-list",  "0, 191, 188"),
        "summary":  ("clipboard-list",  "0, 191, 188"),
        "tldr":     ("clipboard-list",  "0, 191, 188"),
        "info":     ("info",            "8, 109, 221"),
        "todo":     ("check-circle-2",  "8, 109, 221"),
        "tip":      ("flame",           "0, 191, 188"),
        "hint":     ("flame",           "0, 191, 188"),
        "important":("flame",           "0, 191, 188"),
        "success":  ("check",           "8, 185, 78"),
        "check":    ("check",           "8, 185, 78"),
        "done":     ("check",           "8, 185, 78"),
        "question": ("help-circle",     "236, 117, 0"),
        "help":     ("help-circle",     "236, 117, 0"),
        "faq":      ("help-circle",     "236, 117, 0"),
        "warning":  ("alert-triangle",  "236, 117, 0"),
        "caution":  ("alert-triangle",  "236, 117, 0"),
        "attention":("alert-triangle",  "236, 117, 0"),
        "failure":  ("x",               "233, 49, 71"),
        "fail":     ("x",               "233, 49, 71"),
        "missing":  ("x",               "233, 49, 71"),
        "danger":   ("zap",             "233, 49, 71"),
        "error":    ("zap",             "233, 49, 71"),
        "bug":      ("bug",             "233, 49, 71"),
        "example":  ("list",            "120, 82, 238"),
        "quote":    ("quote",           "158, 158, 158"),
        "cite":     ("quote",           "158, 158, 158"),
    }
    lines = text.split("\n")
    result, body, c_type, c_title = [], [], "", ""
    in_c = False

    def flush():
        nonlocal in_c, body
        if not in_c:
            return
        meta = CALLOUT_META.get(c_type.lower(), ("pencil", "8, 109, 221"))
        icon_html = f'<span class="callout-icon"><i data-lucide="{meta[0]}" class="callout-lucide"></i></span>'
        color = meta[1]
        ct = c_type.lower()
        title_inner = f'<span class="callout-title-inner">{c_title or c_type.capitalize()}</span>'
        bhtml = markdown.markdown("\n".join(body), extensions=['tables', 'fenced_code', 'sane_lists'])
        if c_fold:
            open_attr = " open" if c_fold == "+" else ""
            result.append(
                f'<details class="callout" data-callout="{ct}" style="--callout-color: {color};"{open_attr}>'
                f'<summary class="callout-title">{icon_html}{title_inner}'
                f'<i data-lucide="chevron-right" class="callout-fold"></i></summary>'
                f'<div class="callout-content">{bhtml}</div></details>')
        else:
            result.append(
                f'<div class="callout" data-callout="{ct}" style="--callout-color: {color};">'
                f'<div class="callout-title">{icon_html}{title_inner}</div>'
                f'<div class="callout-content">{bhtml}</div></div>')
        in_c = False
        body = []

    c_fold = ""
    for line in lines:
        m = re.match(r'^>\s*\[!(\w+)\]([+-])?\s*(.*)', line)
        if m:
            flush()
            in_c = True
            c_type = m.group(1)
            c_fold = m.group(2) or ""
            c_title = m.group(3).strip()
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

def _resolve_md_images(content: str, file_path: str) -> str:
    """Resolve ![](path) image paths relative to the file or vault root."""
    if not file_path:
        return content
    file_dir = Path(file_path).parent

    def _fix_img(m):
        alt = m.group(1)
        src = m.group(2).strip()
        if src.startswith("http") or src.startswith("/"):
            return m.group(0)
        # Try vault-root relative first
        if (VAULT_ROOT / src).exists():
            return f'![{alt}](/static/{src})'
        # Try file-relative
        rel = file_dir / src
        if (VAULT_ROOT / rel).exists():
            return f'![{alt}](/static/{rel})'
        return m.group(0)

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _fix_img, content)


def render_md(content: str, file_path: str = "") -> str:
    # Strip Obsidian comments %%...%%
    content = re.sub(r'%%.*?%%', '', content, flags=re.DOTALL)
    content = _protect_math(content)  # protect LaTeX from markdown
    content = _resolve_md_images(content, file_path)
    content = render_embeds(content)
    content = render_callouts(content)
    content = render_wiki_links(content)
    content = render_autolinks(content)
    content = re.sub(r'==(.*?)==', r'<mark>\1</mark>', content)
    content = re.sub(r'(?<!\w)#([a-zA-Z0-9_/\u0400-\u04FF\-]+)', r'⟨TAG:\1⟩', content)
    # Convert checkboxes before markdown (standard + alternative markers)
    content = re.sub(r'^(\s*[-*]\s*)\[ \]', r'\1⟨CB:unchecked⟩', content, flags=re.MULTILINE)
    content = re.sub(r'^(\s*[-*]\s*)\[[xX]\]', r'\1⟨CB:checked⟩', content, flags=re.MULTILINE)
    content = re.sub(r'^(\s*[-*]\s*)\[[-/]\]', r'\1⟨CB:cancelled⟩', content, flags=re.MULTILINE)
    content = re.sub(r'^(\s*[-*]\s*)\[[?!>]\]', r'\1⟨CB:other⟩', content, flags=re.MULTILINE)
    html = markdown.markdown(content, extensions=[
        'tables', 'fenced_code', 'codehilite', 'toc', 'nl2br', 'sane_lists', 'smarty', 'footnotes'
    ])
    html = re.sub(r'⟨TAG:(.+?)⟩', r'<span class="tag">#\1</span>', html)
    html = _restore_math(html)
    html = html.replace("<table", '<div class="table-wrap"><table').replace("</table>", "</table></div>")
    # Restore checkboxes and add task-item class to parent <li>
    html = html.replace("⟨CB:unchecked⟩", '<input type="checkbox">')
    html = html.replace("⟨CB:checked⟩", '<input type="checkbox" checked>')
    html = html.replace("⟨CB:cancelled⟩", '<input type="checkbox" checked class="cb-cancelled">')
    html = html.replace("⟨CB:other⟩", '<input type="checkbox" class="cb-other">')
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

    pp = _load_pretty_props()
    pp_pill_colors = pp.get("propertyPillColors", {})
    pp_tag_colors = pp.get("tagColors", {})
    pp_hidden = set(pp.get("hiddenProperties", []))

    # Badges — status with pretty-properties colors
    badges = []
    for f in ("status",):
        vals = meta.get(f, [])
        if isinstance(vals, str):
            vals = [vals]
        for v in (vals if isinstance(vals, list) else []):
            pp_color = pp_pill_colors.get(str(v), {}).get("pillColor", "")
            if pp_color:
                badges.append(_pill_html(v, pp_color))
            else:
                c = status_color(str(v))
                badges.append(f'<span class="badge badge-{c}">{v}</span>')
    # Tags with pretty-properties colors
    for f in ("tags", "tag", "labels", "category"):
        vals = meta.get(f, [])
        if isinstance(vals, str):
            vals = [vals]
        for v in (vals if isinstance(vals, list) else []):
            pp_color = pp_tag_colors.get(str(v), {}).get("pillColor", "")
            if pp_color:
                badges.append(_pill_html(v, pp_color))
            else:
                badges.append(f'<span class="tag">{v}</span>')
    if badges:
        result["badges"] = f'<div class="badges">{"".join(badges)}</div>'

    # Properties — Notion-style, skip hidden
    shown = COVER_FIELDS | BADGE_FIELDS | SKIP_FIELDS | pp_hidden
    props = {k: v for k, v in meta.items() if k.lower() not in shown and k not in pp_hidden and v is not None and str(v).strip()}
    if props:
        # Load Obsidian property types
        types_path = VAULT_ROOT / ".obsidian" / "types.json"
        ob_types = {}
        if types_path.exists():
            try:
                ob_types = json.loads(types_path.read_text(encoding="utf-8")).get("types", {})
            except Exception:
                pass
        _TYPE_ICONS = {
            "text": "text", "multitext": "list", "number": "hash",
            "date": "calendar", "datetime": "clock", "checkbox": "check-square",
            "tags": "tag", "aliases": "forward",
        }
        rows = []
        for k, v in props.items():
            ob_type = ob_types.get(k, "")
            type_icon = _TYPE_ICONS.get(ob_type, "")
            if not type_icon:
                # Auto-detect from value
                if isinstance(v, bool):
                    type_icon = "check-square"
                elif isinstance(v, list):
                    type_icon = "list"
                elif isinstance(v, (int, float)):
                    type_icon = "hash"
                elif isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}', str(v)):
                    type_icon = "calendar"
                else:
                    type_icon = "text"
            # Render value
            if isinstance(v, bool):
                val_html = '<input type="checkbox" disabled checked>' if v else '<input type="checkbox" disabled>'
            elif isinstance(v, list):
                val_html = " ".join(f'<span class="pp-pill">{_escape(str(i))}</span>' for i in v)
            else:
                val_html = _escape(str(v)[:300])
            icon = f'<i data-lucide="{type_icon}" class="prop-icon"></i>'
            rows.append(f'<div class="prop-row">{icon}<span class="prop-key">{_escape(k)}</span><span class="prop-val">{val_html}</span></div>')
        result["props"] = f'<div class="props">{"".join(rows)}</div>'

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

    # property != value  (e.g. status != "done", status != ["archived"])
    m = re.match(r'(\w+)\s*!=\s*(.+)', condition)
    if m:
        key, val_str = m.group(1), m.group(2).strip()
        actual = meta.get(key, "")
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
            return not any(t in actual for t in target)
        if isinstance(actual, list):
            return target not in actual
        return str(actual) != str(target)

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


def _render_card_field(e: dict, field: str) -> str:
    """Render a single field for a card, respecting field type."""
    prop = field.replace("file.", "").replace("note.", "")
    if prop in ("name", "file.name"):
        return ""  # name is rendered separately as title
    if prop == "tags":
        return "".join(f'<span class="tag">{t}</span>' for t in e.get("tags", [])[:4])
    if prop == "status":
        return "".join(f'<span class="badge badge-{status_color(str(s))}">{s}</span>' for s in e.get("status", [])[:2])
    # Generic frontmatter field
    val = e["meta"].get(prop, "")
    if not val:
        return ""
    return f'<span class="card-prop">{_escape(str(val)[:80])}</span>'


def _extract_first_image(fp: Path) -> str:
    """Extract the first image embed from a markdown file (for formula.first_image)."""
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    # Match ![...](path) and ![[path]]
    for m in re.finditer(r'!\[.*?\]\((.+?)\)|!\[\[(.+?)(?:\|.+?)?\]\]', text):
        ref = m.group(1) or m.group(2)
        ref = ref.strip()
        if any(ref.lower().endswith(ext) for ext in IMG_EXTS):
            url = resolve_img(ref)
            if not url:
                # Try relative to file's directory
                rel = fp.parent / ref
                if rel.exists():
                    url = f"/static/{rel.relative_to(VAULT_ROOT)}"
            if url:
                return url
    return ""


def render_base_cards(entries: list[dict], image_field: str = "", aspect: float = 0.5,
                      fields: list[str] = None, card_size: str = "",
                      image_fit: str = "cover") -> str:
    """Render entries as gallery cards with fields from .base order."""
    if not fields:
        fields = ["tags", "status"]

    # Card size → grid column minmax
    size_map = {"small": "160px", "medium": "200px", "large": "280px"}
    min_w = size_map.get(card_size, "200px")
    grid_style = f"grid-template-columns:repeat(auto-fill,minmax({min_w},1fr))"

    # Image fit: cover (crop) or contain (no crop)
    fit = "contain" if image_fit == "contain" else "cover"

    cards = ""
    for e in entries:
        cover = e["cover"]
        if not cover and image_field:
            if image_field.startswith("formula."):
                # Evaluate formula — currently supports first_image
                formula_name = image_field.replace("formula.", "")
                if "first_image" in formula_name:
                    fp = VAULT_ROOT / e["path"]
                    cover = _extract_first_image(fp)
            else:
                prop = image_field.replace("note.", "")
                if prop in e["meta"] and e["meta"][prop]:
                    cover = resolve_img(str(e["meta"][prop]))

        if cover:
            ar_style = f"aspect-ratio:{1/aspect:.2f}" if aspect else "aspect-ratio:2"
            bg_size = "contain" if fit == "contain" else "cover"
            cover_html = f'<div class="card-cover" style="{ar_style};background-image:url(\'{cover}\');background-size:{bg_size};background-position:center;background-repeat:no-repeat"></div>'
        else:
            ar_style = f"aspect-ratio:{1/aspect:.2f}" if aspect else "aspect-ratio:2"
            cover_html = f'<div class="card-cover card-cover-empty" style="{ar_style}"><span>&#128196;</span></div>'

        meta_html = ""
        for field in fields:
            rendered = _render_card_field(e, field)
            if rendered:
                meta_html += f'<div class="card-field">{rendered}</div>'

        card_icon = get_icon_html(e["path"], "")
        cards += f'<div class="card"><a href="/{e["path"]}">{cover_html}<div class="card-body"><div class="card-title">{card_icon}{_escape(e["name"])}</div>{meta_html}</div></a></div>'
    return f'<div class="gallery" style="{grid_style}">{cards}</div>'


def render_base_table(entries: list[dict], columns: list[str] = None,
                      row_height: str = "", column_sizes: dict = None,
                      show_summary: bool = False) -> str:
    """Render entries as a table with specified columns."""
    if not columns:
        columns = ["status", "tags"]
    # Clean column names
    cols = [c.replace("file.", "").replace("note.", "") for c in columns if c != "file.name"]

    # Row height → CSS padding
    rh_map = {"short": "3px 10px", "medium": "6px 10px", "tall": "12px 10px", "extra-tall": "20px 10px"}
    row_pad = rh_map.get(row_height, "6px 10px")

    # Column widths from columnSize config
    col_sizes = column_sizes or {}

    ths = '<th>Name</th>'
    for c in cols:
        w_style = ""
        if c in col_sizes:
            w_style = f' style="width:{int(col_sizes[c])}px"'
        ths += f'<th{w_style}>{_escape(c)}</th>'

    trs = ""
    for e in entries:
        tds = f'<td style="padding:{row_pad}"><a href="/{e["path"]}">{_escape(e["name"])}</a></td>'
        for c in cols:
            if c == "status":
                cell = "".join(f'<span class="badge badge-{status_color(str(s))}">{s}</span>' for s in e["status"])
            elif c in ("tags", "tag"):
                cell = '<div class="cell-tags">' + "".join(f'<span class="tag">{t}</span>' for t in e["tags"]) + '</div>'
            else:
                val = e["meta"].get(c, "")
                cell = _escape(str(val))[:150]
            tds += f'<td style="padding:{row_pad}">{cell}</td>'
        trs += f'<tr>{tds}</tr>'

    summary_html = ""
    if show_summary:
        summary_html = f'<tfoot><tr><td colspan="{len(cols)+1}" style="padding:6px 10px;font-size:12px;color:var(--text2);border-top:2px solid var(--border)">Count: {len(entries)}</td></tr></tfoot>'

    return f'<div class="table-wrap"><table class="db-table"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody>{summary_html}</table></div>'


def render_base_list(entries: list[dict], fields: list[str] = None) -> str:
    """Render entries as a simple bulleted list."""
    if not fields:
        fields = ["status", "tags"]
    rows = ""
    for e in entries:
        meta_parts = []
        for field in fields:
            prop = field.replace("file.", "").replace("note.", "")
            if prop in ("name", "file.name"):
                continue
            if prop == "status" and e["status"]:
                meta_parts.append("".join(
                    f'<span class="badge badge-{status_color(str(s))}">{s}</span>'
                    for s in e["status"][:2]
                ))
            elif prop in ("tags", "tag") and e["tags"]:
                meta_parts.append("".join(f'<span class="tag">{t}</span>' for t in e["tags"][:3]))
            else:
                val = e["meta"].get(prop, "")
                if val:
                    meta_parts.append(f'<span class="card-prop">{_escape(str(val)[:80])}</span>')

        meta_html = f' <span class="db-row-tags">{" ".join(meta_parts)}</span>' if meta_parts else ""
        rows += f'<a class="db-row" href="/{e["path"]}"><div class="db-row-title">{_escape(e["name"])}</div>{meta_html}</a>'
    return f'<div class="db-list">{rows}</div>'


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

def _load_bookmarks() -> list[dict]:
    """Load bookmarks from .obsidian/bookmarks.json."""
    bm_path = VAULT_ROOT / ".obsidian" / "bookmarks.json"
    if not bm_path.exists():
        return []
    try:
        import json
        data = json.loads(bm_path.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def build_bookmarks_html(current_path: str = "") -> str:
    """Render bookmarks section from .obsidian/bookmarks.json."""
    bm_list = _load_bookmarks()
    if not bm_list:
        return ""
    items = ""
    for entry in bm_list:
        path = entry.get("path", "")
        label = entry.get("title", "")
        if not path:
            continue
        fp = VAULT_ROOT / path
        if not fp.exists():
            continue
        if not label:
            label = fp.stem if fp.is_file() else fp.name
        raw = get_raw_icon(path)
        icon = get_icon_html(path, "") if raw else ""
        active = "active" if path == current_path else ""
        items += f'<a class="tree-item bm-item {active}" href="/{path}">{icon}{_escape(label)}</a>'
    if not items:
        return ""
    return f'<div class="bookmarks">{items}</div>'


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
    bookmarks_html = build_bookmarks_html(current_path)

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
    theme_css = _load_theme(CONFIG.get("theme", ""))
    theme_style = f"<style>{theme_css}</style>" if theme_css else ""
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
{theme_style}
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
    {bookmarks_html}
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


def _group_entries(entries: list[dict], group_by: dict) -> list[tuple[str, list[dict]]]:
    """Group entries by a property. Returns list of (group_label, entries) tuples."""
    prop = group_by.get("property", "").replace("file.", "").replace("note.", "")
    desc = group_by.get("direction", "ASC").upper() == "DESC"
    if not prop:
        return [("", entries)]

    groups: dict[str, list[dict]] = {}
    for e in entries:
        val = e["meta"].get(prop, "")
        if isinstance(val, list):
            keys = [str(v) for v in val] if val else ["(empty)"]
        else:
            keys = [str(val) if val else "(empty)"]
        for k in keys:
            groups.setdefault(k, []).append(e)

    sorted_keys = sorted(groups.keys(), key=str.lower, reverse=desc)
    return [(k, groups[k]) for k in sorted_keys]


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

    # New settings
    card_size = view.get("cardSize", "")
    image_fit = view.get("imageFit", "cover")
    row_height = view.get("rowHeight", "")
    column_sizes = view.get("columnSize", {})
    limit = view.get("limit", 0)
    group_by = view.get("groupBy", {})
    summaries = view.get("summaries", False)

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

    total_count = len(entries)

    # Apply limit
    if limit and limit > 0:
        entries = entries[:limit]

    title = fp.stem

    # Icon — same as regular .md pages
    parts = get_page_parts({}, file_path)
    has_icon = bool(get_raw_icon(file_path))
    wrapper_cls = "no-cover has-icon" if has_icon else "no-cover"
    page_title = f'<h1 style="font-size:2em;font-weight:700;margin:0 0 0.3em;font-family:var(--font)">{_escape(title)}</h1>'
    header = f'<div class="{wrapper_cls}">{parts["icon"]}{page_title}</div>'
    limit_note = f" (showing {len(entries)})" if limit and limit < total_count else ""
    info = f'<div class="filter-info" style="margin-bottom:12px">{total_count} items{limit_note}</div>'

    def _render_view_body(view_entries: list[dict]) -> str:
        if view_type == "cards":
            return render_base_cards(view_entries, image_field, aspect, fields=columns,
                                     card_size=card_size, image_fit=image_fit)
        elif view_type == "list":
            return render_base_list(view_entries, fields=columns)
        else:
            return render_base_table(view_entries, columns, row_height=row_height,
                                      column_sizes=column_sizes,
                                      show_summary=bool(summaries))

    # GroupBy
    if group_by and group_by.get("property"):
        groups = _group_entries(entries, group_by)
        body = ""
        for label, group_entries in groups:
            body += f'<h3 style="font-size:15px;font-weight:600;margin:18px 0 8px;color:var(--text2)">{_escape(label)} <span style="font-weight:400;font-size:12px">({len(group_entries)})</span></h3>'
            body += _render_view_body(group_entries)
    else:
        body = _render_view_body(entries)

    content = header + tabs_html + info + body
    page_icon = get_raw_icon(file_path)
    return layout(f"{title} — Base", content, file_path, page_icon=page_icon)


@app.get("/base/{dir_path:path}", response_class=HTMLResponse)
async def base_view(dir_path: str, view: str = Query("cards")):
    """Database-like view of a directory: cards, list, or table."""
    dp = safe_path(dir_path)
    if not dp.is_dir():
        raise HTTPException(404, "Not a directory")

    # If directory contains a .base file, delegate to render_base_view
    base_files = list(dp.glob("*.base"))
    if base_files:
        tab = 0  # default to first tab
        return render_base_view(base_files[0], str(base_files[0].relative_to(VAULT_ROOT)), tab)

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

    md_html = render_md(post.content, file_path)

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
