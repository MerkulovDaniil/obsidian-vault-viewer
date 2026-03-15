#!/bin/sh
# Background: reset docs from pristine copy every 15 min
while true; do
    sleep 900
    cp -a /docs-pristine/* /docs/ 2>/dev/null
    cp -a /docs-pristine/.obsidian /docs/ 2>/dev/null
done &

silmaril --vault /docs --port ${PORT:-8080} --title "Silmaril" --host 0.0.0.0
