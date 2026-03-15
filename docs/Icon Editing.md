---
status: active
tags:
  - docs
  - features
---

# Icon Editing

Silmaril supports the Obsidian **Iconic** plugin — and lets you edit icons directly from the browser.

## How it works

1. Click any page icon (or the `+` placeholder on pages without one)
2. Choose from **emoji** or **Lucide** icons
3. Optionally pick a color
4. Done — saved to Iconic's `data.json`, synced with Obsidian

## Emoji tab

~90 popular emoji with keyword search. Type "star" to find ⭐, "fire" for 🔥.

You can also paste any custom emoji in the text field.

## Lucide tab

All ~1900 Lucide icons, searchable by name. Type "file", "heart", "code" — the grid filters instantly.

## Color picker

Optional color tint for any icon. Click "Reset" to remove.

## Remove icon

Click the trash button to remove an icon assignment.

## API

Under the hood:

```
POST /api/icon/path/to/file.md
{"icon": "🔥", "color": "#ff6600"}

DELETE /api/icon/path/to/file.md
```

Changes are written directly to `.obsidian/plugins/iconic/data.json`.

> [!tip] Try it now
> Click the `+` icon at the top of this page to assign an icon!
