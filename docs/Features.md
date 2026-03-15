---
banner: https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200
status: active
tags:
  - docs
---

# Features

Silmaril renders most of what Obsidian renders — from a single Python file.

## Markdown

Full Obsidian-flavored markdown:
- **Bold**, *italic*, ~~strikethrough~~, ==highlights==
- `inline code` and fenced code blocks with copy button
- Blockquotes, nested lists, tables
- Checkboxes: `- [x]` and `- [ ]`

## Wiki-links

`[[Page Name]]` links are resolved across the entire vault. `![[image.png]]` embeds work too.

## Callouts

> [!note] This is a note
> Supports all Obsidian callout types.

> [!warning] Warning callout
> With custom titles and nesting.

> [!tip] Pro tip
> Callouts render with proper icons and colors.

## Math

Inline math: $E = mc^2$

Display math:

$$\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}$$

## Frontmatter

Status badges, tags, cover images, and all properties are rendered from YAML frontmatter — just like this page.

## Obsidian Bases

`.base` files render as cards, lists, or tables — the same database views you use in Obsidian.

## Iconic Plugin

Lucide icons and emoji from the Iconic plugin render in the sidebar and page headers. You can **edit icons directly** from the web UI — click any page icon to open the picker.

## Code Blocks

```python
from silmaril import app

# That's the entire application
# One file, zero config
```

Every code block gets a copy button on hover.

## Search

Full-text search with instant sidebar filtering. Type in the sidebar search — file names filter in real-time, content matches appear below.

## Mobile

Responsive design that works on any screen. Collapsible sidebar, touch-friendly, pinch-to-zoom support.
