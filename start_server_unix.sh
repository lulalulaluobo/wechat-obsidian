#!/bin/bash
set -a
source .env
set +a
nohup .venv-unix/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 > server.stdout.log 2> server.stderr.log &
