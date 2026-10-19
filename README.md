# Voice Comparison Studio

Compare Qwen3-TTS, CosyVoice 3 and XTTS-v2 against one server-managed reference voice. The React frontend plays actual recordings; FastAPI schedules synthesis and stores measured results in SQLite. The original voice has audio and a transcript, without generated scores.

Available models run **simultaneously**. Each model serializes its own requests; there is no global GPU lock, performance mode, generated placeholder audio or silent substitution of one model for another. Use sufficient GPU memory for concurrent models, or put individual workers on different GPUs. Running concurrently does not guarantee faster inference on a saturated GPU.

## Quick start

Use Python 3.12 for the API and Node 22 for the frontend. Model environments may require a different Python/PyTorch combination; see below. Commands start at the repository root.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
cd frontend
npm ci
npm run build
cd ../backend
python -m uvicorn backend:app --host 127.0.0.1 --port 8000 --workers 1
```

Open **http://localhost:8000**. The production build and API share one address. Without a frontend build, port 8000 returns service information explaining how to open the frontend. For development, run `npm run dev` in `frontend/`, open http://localhost:5173 and keep the API running on port 8000. Vite proxies `/api` to the API.

On Windows, activate `.venv\Scripts\activate` and use `copy backend\.env.example backend\.env` instead of the two POSIX commands above. All Python commands use `python -m ...` so they use the activated environment rather than a different executable on PATH.

The API starts without model packages. Generation stays unavailable until at least one engine or worker is configured. `requirements.txt` deliberately does not install three incompatible ML dependency stacks into the API environment.

## Reference audio

The supplied recording and transcript are in `backend/reference/reference.wav` and `reference.txt`. Replace both together with your reference recording and its exact transcript, then restart. The recording must be a PCM WAV lasting 0.25–120 seconds. Use an absolute `REFERENCE_DIR` for a separately managed reference directory. The app imports it into persistent storage and keeps its ID stable when unchanged.

Only administrators can reload the reference through `POST /api/admin/reload-reference`, with `Authorization: Bearer <ADMIN_TOKEN>`. Public upload and transcript-edit routes have been removed. The browser can listen to the original recording and read its transcript, so these are not secret from authorized users.

To preserve previous recordings, copy your **entire** existing `backend/data/` directory before first startup. Do not overwrite or delete the existing database. Startup repairs missing absolute paths when the same filenames exist in the new data/audio directory, and marks interrupted jobs failed so the UI does not poll forever. Keep one API process (`--workers 1`) because job ownership is in process memory.

## Fix the missing model errors

Run this using the same interpreter as the API:

```bash
cd backend
python doctor.py
```

It prints the interpreter path, reference availability, authentication status and per-model setup diagnostics. Exit code 1 means at least one model/reference still needs setup. Model availability checks do not download weights or prove inference quality. Local adapters load weights on first generation; worker preloading reports loading errors earlier.

| Screenshot error | Cause | Fix |
| --- | --- | --- |
| `No module named 'cosyvoice'` | Official source repository not importable in the engine environment | Install the official repository and its dependencies, set `PYTHONPATH` and `COSY_MODEL_DIR`, or configure its isolated worker |
| `No module named 'TTS'` | Coqui engine not installed in the engine environment | Install the maintained `coqui-tts` distribution; its import namespace is `TTS` |
| Port 8000 `Not Found` | API had no homepage | Build the frontend before starting the updated API; it now serves the website |
| Only Qwen generates | Other engine dependencies/checkpoints unavailable | Follow diagnostics; unavailable models are skipped visibly and never counted as successful |

### Local adapters

If an existing environment already supports all engines, leave worker URLs unset and run the API there. Install PyTorch/torchaudio builds appropriate for the host GPU first, then model dependencies according to the publishers. `python -m pip install qwen-tts` installs the Qwen package; `python -m pip install coqui-tts` installs the maintained Coqui package. CosyVoice requires its source repository and checkpoints, not a guessed `pip install cosyvoice` command.

### Isolated workers (recommended for dependency conflicts)

Each worker runs from `backend/` with its own Python environment. The API remains in `.venv`. Set these in `backend/.env`:

```dotenv
WORKER_QWEN3_URL=http://127.0.0.1:8101
WORKER_COSYVOICE3_URL=http://127.0.0.1:8102
WORKER_XTTS_V2_URL=http://127.0.0.1:8103
VOICE_STUDIO_DATA=/absolute/path/to/Speech-cloning/backend/data
WORKER_TOKEN=your-long-random-worker-token
GENERATION_LANGUAGE=German
```

All workers and the API must see the same absolute `VOICE_STUDIO_DATA` directory. Workers read the imported reference there; they do not need database access. This implementation supports colocated processes or an identical shared mount, not arbitrary remote hosts without that mount. Leave the URL unset for a model you want to run locally.

1. **Qwen:** activate your working `qwen3` environment, install `backend/workers/requirements.txt` and `qwen-tts` using that environment's Python, then run `bash backend/models/qwen3/start.sh` from the repository root. Keep this terminal open. Default checkpoint: `Qwen/Qwen3-TTS-12Hz-1.7B-Base`.
2. **CosyVoice:** create the environment specified by the [official repository](https://github.com/FunAudioLLM/CosyVoice), clone it with `git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git`, install its documented requirements, and download the official CosyVoice 3 checkpoint. Set `COSY_MODEL_DIR` to the downloaded checkpoint directory. Export `PYTHONPATH=/absolute/path/to/CosyVoice:/absolute/path/to/CosyVoice/third_party/Matcha-TTS`, install this project's worker requirements in that environment, then run `bash backend/models/cosyvoice3/start.sh`. The adapter uses `AutoModel` and the CosyVoice 3 prompt prefix.
3. **XTTS:** create a separate environment compatible with [maintained Coqui TTS](https://github.com/idiap/coqui-ai-TTS), install the appropriate PyTorch/torchaudio build, `coqui-tts` and this project's worker requirements, then run `bash backend/models/xtts_v2/start.sh`. Review the checkpoint's license and complete any required license acceptance before unattended deployment. The app does not accept license terms for you.

On Windows, run workers from `backend/` directly: set `MODEL_ID` to the corresponding model, then `python -m uvicorn workers.serve:app --host 127.0.0.1 --port 8101 --workers 1` (ports 8102 and 8103 for the other models).

Optional environment overrides: `QWEN_MODEL`, `XTTS_MODEL`, `MODEL_DEVICE` (Qwen/XTTS); use `CUDA_VISIBLE_DEVICES` to assign GPUs to individual worker processes, including CosyVoice, `PRELOAD_MODELS=0` (load on first call). Existing shell exports override `.env`. All processes must use the same `GENERATION_LANGUAGE`. Do not expose worker ports publicly. A missing worker is reported as unavailable; generation never silently changes engines.

## Numerical evaluation

Duration, generation time and real-time factor (generation seconds / audio seconds) need no evaluator model. Runtime excludes waiting for the model lock but includes cold loading when applicable. Cache hits reuse the original measured generation time. Change `MODEL_CACHE_VERSION` when replacing checkpoints or inference settings; language and common checkpoint settings are also part of the cache key.

Optional evaluators run on CPU by default:

```bash
# In the API environment; ffmpeg must also be installed for Whisper.
python -m pip install openai-whisper jiwer
# Set ENABLE_WHISPER=1 in backend/.env, then restart.
```

`ENABLE_ECAPA=1` requires SpeechBrain, PyTorch, torchaudio, soundfile and scipy. `ENABLE_SCOREQ=1` requires ScoreQ and its dependencies. Install using the [Qwen](https://github.com/QwenLM/Qwen3-TTS), [SpeechBrain](https://github.com/speechbrain/speechbrain), [Whisper](https://github.com/openai/whisper) and [ScoreQ](https://github.com/speechmatics/scoreq) publisher instructions. Dependencies and weights are not downloaded during CI.

WER/CER compare normalized lowercase text without punctuation; they measure the ASR transcript's agreement with the requested text, not voice identity. ECAPA measures speaker similarity. No single composite “best model” score is invented. Missing evaluators produce no substitute values. Enabled evaluators that fail show persistent errors on every affected recording. Metrics update automatically while evaluation runs, and failed/interrupted evaluations can be retried.

Human naturalness ratings apply to a specifically selected model recording. Cohen's kappa compares the first two raters alphabetically across shared recordings; it is not an audio quality metric. Undefined agreement returns null rather than invalid JSON. Research batch/export endpoints remain available through the API; batch repetitions bypass the cache, and batch status includes its generation records; the UI has only Compare and Metrics.

## Deployment

The supported authenticated deployment is **one HTTPS origin** serving the frontend and API together. This is a private, single-owner studio: access-token holders share the same recordings, workspace and ratings. It is not a multi-tenant public service.

1. Install the API and model environments on a GPU host, set an absolute persistent `VOICE_STUDIO_DATA`, and build the frontend as above.
2. Set long random `API_TOKEN`, `WORKER_TOKEN` and a different `ADMIN_TOKEN` in backend/.env. Generate each with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Set `COOKIE_SECURE=1` behind HTTPS and `CORS_ORIGINS=https://your-domain.example`.
3. Start workers privately on loopback. Use the supplied systemd unit as a template for the API and equivalent units for workers, adjusting paths, user and model environments. Keep one API process.
4. Adapt `deploy/Caddyfile` to your domain and proxy to port 8000. Caddy supplies HTTPS. The UI prompts for the API access token and uses an expiring HttpOnly session cookie, including for audio playback. Never compile a secret into `VITE_*` variables.
5. Back up the persistent database/audio directory while the service is stopped. Monitor storage, GPU memory and job failures. The in-memory queue is bounded; it rejects excess work with HTTP 429. Stopped jobs are not automatically retried.

`deploy/voice-studio.service` is an editable example, not an installed service. This repository does not provision a host, domain, TLS certificates or GPU resources. No Docker is required.

The optional GitHub Pages workflow is manual-only and builds just the frontend. Set repository variable `VITE_API_URL` to a reachable HTTPS API and configure the Pages base path. A Pages site cannot run Python or GPU inference. Cross-site session cookies are intentionally unsupported by the default SameSite policy; use the single-origin setup for authenticated use. GitHub Pages alone does not make this project deployable end to end.

## Verification

```bash
python -m pip install -r backend/requirements.txt
python -m pytest backend/tests -q
cd frontend
npm ci
npm run build
npm audit --audit-level=moderate
```

CI checks API behavior and the frontend build without model downloads. Tests use clearly isolated adapter fakes for dispatch/concurrency, not real synthesis. Actual Qwen/CosyVoice/XTTS inference, memory capacity, output quality and optional evaluator model compatibility must be checked on the deployment GPU host. See [AUDIT.md](AUDIT.md) for the findings and validation boundary.
