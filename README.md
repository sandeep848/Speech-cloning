# Voice Comparison Studio

Two top-level application folders only: `frontend/` (React/Vite; deployable to GitHub Pages) and `backend/` (FastAPI and isolated model workers). No Docker is required. **GitHub Pages only hosts the frontend.** Python models require a separately running backend with reachable HTTPS API for deployment.

## Reference: server-only, persisted

Put your real reference audio at **`backend/reference/reference.wav`** and its exact spoken transcript at **`backend/reference/reference.txt`**. No example audio is bundled; you provide your own authorized voice recording. The file `reference.txt` currently contains instructions, **not a real transcript**; replace them before using CosyVoice.

Reference audio must be valid PCM WAV, 0.25–120 seconds. To normalize any recording:

```bash
ffmpeg -i my_reference.mp3 -ac 1 -ar 24000 -c:a pcm_s16le backend/reference/reference.wav
```

Update the transcript by editing `backend/reference/reference.txt`. Restart FastAPI to import changes, or (if configured) use the protected `POST /api/admin/reload-reference` with `ADMIN_TOKEN`. This persists the content to the backend `data/` folder. Do not commit your audio to a public GitHub repo. The website displays the configured reference and transcript read-only; it cannot replace them. **You do not have to re-upload or retype the reference per generation.**

## Local start

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn backend:app --host 127.0.0.1 --port 8000 --workers 1
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The frontend's development proxy forwards `/api` to 8000.

## Model workers (separate terminals, separate environments)

The API itself does not load model weights. Install each model's correct pinned package dependencies and checkpoint in an environment appropriate to that engine. Execute from `backend/`:

```bash
# Qwen3-TTS environment
bash models/qwen3/start.sh
# CosyVoice 3 environment
COSY_MODEL_DIR=/absolute/path/to/cosyvoice-checkpoint bash models/cosyvoice3/start.sh
# XTTS-v2 environment
bash models/xtts_v2/start.sh
```

Configure `backend/.env` (or `export` shell variables) before starting the API:

```env
WORKER_QWEN3_URL=http://127.0.0.1:8101
WORKER_COSYVOICE3_URL=http://127.0.0.1:8102
WORKER_XTTS_V2_URL=http://127.0.0.1:8103
VOICE_STUDIO_DATA=/absolute/path/to/voice-studio-github-pages/backend/data
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://YOURNAME.github.io
ADMIN_TOKEN=long_random_admin_secret
```

The shell or process manager must load variables; `.env` is documentation rather than being auto-loaded. Model workers must share `VOICE_STUDIO_DATA` with the API when using local reference paths. The default job executor is **sequential** on a shared GPU (`--workers 1` for the API), as requested for the current environment. Moving model cards doesn't load or regenerate voices.

## Publish on GitHub Pages

1. Push this project to a GitHub repository; do not add backend/reference/reference.wav, backend/data, secrets or weights.
2. Deploy a secured, HTTPS-accessible FastAPI server separately; the browser must be able to reach it. Never expose worker ports or PostgreSQL/SQLite directly.
3. Repository Settings → Actions → General: enable actions. Settings → Pages: select **GitHub Actions** as build source.
4. Repository Settings → Secrets and variables → Actions → Variables: set `VITE_API_URL` to the full HTTPS URL of your backend, without a trailing slash. This URL is public by design, never a secret.
5. Push to `main`; `.github/workflows/deploy.yml` builds and deploys the frontend with the correct repository base path.

**Important:** This single-user development backend does not provide public-user authentication and access isolation. Do not expose it publicly without adding authentication, rate limits, per-user storage authorization and secure audio access. GitHub Pages alone cannot provide GPU inference or backend uptime.

## Status and troubleshooting

`GET /api/models` reports readiness only when the corresponding worker is running and its engine loaded. If the worker is offline, UI shows unavailable. There is no synthetic audio fallback. If generating fails, inspect the separate model worker's terminal. Actual inference depends on compatible checkpoints, model licenses and GPU memory. The three integrations have **not** been executed against your own GPUs in this environment.

## Frontend verification

Run the same TypeScript and production-build checks used by GitHub Actions:

```bash
cd frontend
npm install
npm run build
```

The `src/vite-env.d.ts` declaration enables TypeScript to recognize `import.meta.env`. The GitHub Pages deployment also depends on repository Pages settings and a reachable HTTPS backend. A successful static build **does not** establish that speech-generation workers or authentication are production-ready.

**Security boundary:** `VITE_API_URL` is embedded in browser code, so never use it to store secrets. Configure secrets only in the backend environment. Avoid publicly exposing this API until authentication, authorization and rate limiting have been independently tested.
