#!/bin/sh
set -eu

bot_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
bot_python="$bot_dir/.venv/bin/python"
if [ ! -x "$bot_python" ]; then
    if command -v python3.10 >/dev/null 2>&1; then
        python3.10 -m venv "$bot_dir/.venv"
    else
        python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or newer is required.")'
        python3 -m venv "$bot_dir/.venv"
    fi
fi
"$bot_python" "$bot_dir/scripts/sync_requirements.py"
exec "$bot_python" "$bot_dir/run_bot.py" --data-dir "$bot_dir" "$@"
