#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../.."
export MODEL_ID="xtts-v2"
export VOICE_STUDIO_DATA="${VOICE_STUDIO_DATA:-$PWD/data}"
exec python -m uvicorn workers.serve:app --host 127.0.0.1 --port 8103 --workers 1
