#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../.."
export MODEL_ID="cosyvoice3"
export VOICE_STUDIO_DATA="${VOICE_STUDIO_DATA:-$PWD/data}"
exec uvicorn workers.serve:app --host 127.0.0.1 --port 8102 --workers 1
