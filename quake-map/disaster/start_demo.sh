#!/bin/sh
# One command that starts the whole demo (dashboard, simulation, drones, phone links, Kindle). See tools/start_demo.py --help.
cd "$(dirname "$0")" || exit 1
HERE=$(pwd -P)
for v in "$HERE/../../.venv" "$HERE/.venv"; do
    d=$(cd "$v" 2>/dev/null && pwd -P) && [ -x "$d/bin/python" ] && exec "$d/bin/python" "$HERE/tools/start_demo.py" "$@"
done
exec python3 "$HERE/tools/start_demo.py" "$@"
