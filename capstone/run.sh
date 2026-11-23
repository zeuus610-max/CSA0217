#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install 'fastapi>=0.115,<1.0' 'uvicorn[standard]>=0.30,<1.0'
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
exec python -m uvicorn server:app --host 127.0.0.1 --port 8765
