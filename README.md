# Voice Comparison Studio

Voice Comparison Studio is a research prototype for comparing **three zero-shot voice-cloning text-to-speech (TTS) engines** under a shared workflow:

- [Qwen3-TTS 12Hz 1.7B Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)
- [Fun-CosyVoice3 0.5B](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
- [Coqui XTTS-v2](https://huggingface.co/coqui/XTTS-v2)

It provides a React/TypeScript interface, a Python FastAPI backend, reference-voice handling, text generation, saved WAV outputs, and automated evaluation. The engines use a **single Python runtime** but may execute jobs concurrently. This is an experimental comparison tool, not a claim of production-ready cloning quality or real-time performance.

> **Research and consent:** Use reference recordings only when you have appropriate permission. Generated speech can contain pronunciation errors, identity drift, or audible synthesis artifacts. Do not present generated speech as a person's authentic recording.

## Quick start

### Environment

Use a dedicated Python environment. **Python 3.12** is used by this project's CI and is the recommended project baseline; upstream model stacks have their own compatibility constraints. Node.js **22** is used by the repository's CI and deployment workflows. FFmpeg and git should be available on the host. NVIDIA CUDA is optional, but a compatible GPU can be important for practical inference throughput.

```bash
git clone https://github.com/sandeep848/Speech-cloning.git
cd Speech-cloning

# Example, if conda is available
conda create -n qwen3 python=3.12 -y
conda activate qwen3

cp backend/.env.example backend/.env
python start.py --no-browser
```

Open **http://127.0.0.1:8000** when startup completes.

The launcher can install missing Python packages, build the frontend using npm, obtain model weights, preload engines/evaluators, and start the API. First launch can be resource-intensive and requires internet access. It may not work unchanged on every OS or university host because CosyVoice and model-specific native dependencies can require additional setup.

`start.py` options:

```bash
python start.py --no-browser
python start.py --port 8001 --no-browser
python start.py --verify-only
```

`--verify-only` loads models and attempts synthesis/evaluation; it is not a lightweight environment check. Run `python backend/doctor.py` for a lower-cost diagnostic, or `python system_profile.py` for hardware and configuration details.

### Reference audio

Use a clean recording of **one speaker**, together with an **accurate transcript** of exactly what was spoken. By default, the backend reads:

- `backend/reference/reference.wav`
- `backend/reference/reference.txt`

For the English reference-profile endpoint, it looks for `reference_en.wav` and `reference_en.txt`. Reference WAV uploads are also supported in the application. The upload path accepts PCM WAV audio with a duration of **0.25–120 seconds**. Reference cleanliness, language, and transcript accuracy materially affect output quality; longer audio is not automatically better.

The repository may include example reference recordings. Do not assume they are appropriate for your testing, sharing, or redistribution.

## Models and upstream documentation

| Adapter ID | Upstream model | Repo adapter | Documentation |
| --- | --- | --- | --- |
| `qwen3` | Qwen3-TTS 12Hz 1.7B Base | `backend/models/qwen3/model.py` | [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) |
| `cosyvoice3` | Fun-CosyVoice3 0.5B | `backend/models/cosyvoice3/model.py` | [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) |
| `xtts-v2` | Coqui XTTS-v2 | `backend/models/xtts_v2/model.py` | [XTTS documentation](https://coqui-tts.readthedocs.io/en/latest/models/xtts.html) |

The adapters differ in their reference conditioning, generation controls, model architectures, and dependency requirements. Consult the upstream model cards and repositories for supported platforms, model licenses, known limitations, and version-specific setup. Do not transfer benchmarks or hardware claims from one model generation to another.

### Minimum CPU, RAM, and VRAM requirements

**Do not treat parameter counts or estimated VRAM ranges as official minimum system requirements.** This repository does not contain a reproducible, verified minimum-hardware matrix for these exact three models. The upstream references linked above do not establish one universal RAM/VRAM requirement and CPU/GPU time for all possible backends and precisions.

Actual memory and performance depend on installed weights, tensor dtype, reference and output length, batching, optional acceleration, library versions, device, and other processes. Determine suitability for **your environment** using the profiler below. A CPU-only PyTorch installation cannot benchmark CUDA even when the host physically contains a GPU.

## Natural speech and pacing

The shared pipeline groups text into sentence/phrase chunks, generates model-specific audio, trims/caps some silent regions, and joins the resulting clips with punctuation-aware pauses. As of the current code, **commas are not split by default**; semicolons, colons, sentence endings, paragraph boundaries, and long-word-bounded chunks can still introduce boundaries.

The current in-code defaults are:

| Setting | Default | Behavior |
| --- | --- | --- |
| `QWEN_SPEECH_RATE` | `1.0` | Native Qwen speed; values below 1 use pitch-preserving time stretching |
| `COSY_SPEECH_RATE` | `1.0` | CosyVoice native speed parameter |
| `XTTS_SPEECH_RATE` | `1.0` | Multiplied by `XTTS_SPEED` for XTTS native inference |
| `SPLIT_ON_COMMA` | `0` | Set to `1` to restore explicit comma splitting |
| `COMMA_PAUSE_MS` | `120` | Used at configured comma splits and other short phrase boundaries |
| `SENTENCE_PAUSE_MS` | `260` | Target sentence-boundary pause |
| `PARAGRAPH_PAUSE_MS` | `450` | Target blank-line paragraph pause |
| `CONTINUATION_PAUSE_MS` | `90` | Target continuation pause for long text |
| `MAX_INTERNAL_PAUSE_MS` | `650` in code | Limits sustained quiet internal gaps; `.env.example` currently sets `900` |

**Environment precedence:** shell variables override `backend/.env`; `backend/.env` overrides code defaults. Copying `.env.example` establishes its own values. The previously used speed/pauses may remain active on existing installations until you change or unset the relevant variables and restart the backend.

Other relevant configuration options include `QWEN_TEMPERATURE`, `QWEN_TOP_P`, `QWEN_TOP_K`, `QWEN_BATCH_SIZE`, `XTTS_TEMPERATURE`, `XTTS_REPETITION_PENALTY`, `XTTS_SPEED`, and optional CosyVoice acceleration flags. The Qwen adapter uses SDPA by default when available; FlashAttention 2 is opt-in and requires a supported environment.

**Naturalness is not guaranteed by configuration alone.** For a fair comparison, use the same reference and text; listen for rhythm, pitch, pronunciation, breaks, clipping, and speaker consistency. Scores from automated metrics do not replace listening.

## Inspect your hardware and actual configuration

```bash
python system_profile.py
```

The script writes `system-profile.json` and reports the current interpreter, operating system, CPU information, RAM when `psutil` is installed, PyTorch/CUDA availability, GPU name/VRAM, package versions, and TTS settings. This command **does not load model weights**.

For more complete hardware reporting:

```bash
python -m pip install psutil
python system_profile.py
```

The JSON can contain local paths and configuration information; review it before sharing.

## Measure real CPU and GPU inference time

The profiler measures actual generation through this repository's adapters, with a separate process for each model/device combination. It distinguishes **model load**, **warmup generation**, and **repeated inference**.

```bash
# Compare available devices; GPU entries are marked unavailable without CUDA
python system_profile.py --benchmark \
  --models qwen3 cosyvoice3 xtts-v2 \
  --devices cpu gpu \
  --repetitions 3

# A smaller CPU-only experiment
python system_profile.py --benchmark \
  --models qwen3 \
  --devices cpu \
  --repetitions 3
```

The benchmark requires the reference WAV and its transcript. It may download model weights and consume substantial CPU, RAM, GPU memory, and disk space.

| Report field | Interpretation |
| --- | --- |
| `load_seconds` | Initial model initialization time |
| `warmup_seconds` | First generation, excluded from steady-state trials |
| `median_seconds` | Median wall-clock generation duration |
| `median_rtf` | Generation seconds divided by generated audio seconds; below 1 is faster than real-time |
| `peak_gpu_allocated_gb` | Peak memory allocated by PyTorch on GPU (not total system/device utilization) |
| `gpu_speedup` | CPU median divided by GPU median, **only** when both runs succeeded |

CPU and GPU timings are **not predetermined**. GPU measurements are unavailable when the current environment cannot access CUDA. For reliable comparisons, retain the same input, reference, language, model weights, and settings; avoid competing workloads. Benchmark results describe the measured machine, not all deployments.

See [SYSTEM_PROFILE.md](SYSTEM_PROFILE.md) for details. A separate research-oriented benchmark with saved WAV files and evaluation statistics is available:

```bash
python backend/benchmark.py --language German --repetitions 3
```

This benchmark uses model generation and may load evaluation systems; disable unneeded evaluators if resources are constrained.

## Evaluation

The backend includes these measurements, subject to dependencies and per-metric availability:

- **WER/CER:** Whisper recognition compared with requested text, using jiwer.
- **ECAPA speaker similarity:** SpeechBrain embeddings comparing reference and output.
- **SCOREQ:** A non-intrusive estimated speech-quality score.
- **DNSMOS:** Speech/noise/overall quality estimation when the necessary model can run.
- **Latency and RTF:** Time and throughput measurements associated with synthesis.

These are different dimensions. A lower WER does not prove better naturalness; a similarity score does not prove the output is free of artifacts. An unavailable metric should be recorded as unavailable rather than invented. F0 Frame Error Rate is not implemented as a valid aligned pitch-error metric for unrelated reference/output utterances.

The `backend/.env.example` template disables Whisper, ECAPA, and SCOREQ by default, while the launcher may enable some evaluators when those settings are not explicitly present. Check the **effective** environment before comparing scores.

## Project layout

```text
Speech-cloning/
├── start.py                    # Launcher / optional verification
├── system_profile.py           # Configuration and CPU/GPU benchmark
├── SYSTEM_PROFILE.md           # Profiling guide
├── backend/
│   ├── backend.py              # FastAPI routes and SQLite persistence
│   ├── direct_inference.py     # Synthesis interface
│   ├── doctor.py               # Reference and model diagnostics
│   ├── benchmark.py            # Multi-script research benchmark
│   ├── evaluator.py            # Automated acoustic/speech metrics
│   ├── .env.example            # Environment configuration example
│   ├── requirements.txt        # Python packages
│   ├── models/
│   │   ├── pacing.py           # Text phrasing and rate control
│   │   ├── qwen3/
│   │   ├── cosyvoice3/
│   │   └── xtts_v2/
│   ├── reference/              # Reference WAV and transcript files
│   └── tests/                  # Backend regression tests
├── frontend/
│   ├── src/                    # React + TypeScript UI
│   └── package.json
├── deploy/                     # Caddy and systemd templates
└── .github/workflows/          # CI and Pages deployment
```

## Test and troubleshoot

```bash
# Confirm interpreter and available model adapters
python -c "import sys; print(sys.executable)"
python backend/doctor.py

# Backend regression tests
python -m pytest backend/tests -q

# Frontend build
cd frontend
npm ci
npm run build
cd ..

# Backend health endpoint (after starting the server)
curl http://127.0.0.1:8000/api/health
```

If the backend cannot import dependencies, use `python -m pip` from the **same environment** as the running Python interpreter. `start.py` may switch into a local `qwen3` conda environment if necessary, so check its printed interpreter path. If the frontend cannot reach the API, confirm that the server is running, inspect the browser console and CORS settings, and check the configured API URL.

For GPU detection, compare `nvidia-smi` (when available) with:

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

PyTorch `+cpu` builds will not use CUDA. A CPU/GPU time comparison must be executed rather than inferred from GPU name or parameter count.

## Deployment and security limitations

The React frontend can be built as static files. GitHub Pages deployment is defined in `.github/workflows/deploy.yml` and depends on a reachable backend URL configured with `VITE_API_URL`. **GitHub Pages does not host the Python inference backend.** The backend must run independently and be reachable over HTTPS when the frontend is served over HTTPS.

``backend/.env`` can hold `CORS_ORIGINS`, `API_TOKEN`, `ADMIN_TOKEN`, and model paths. **Do not interpret the presence of token configuration variables as proof that all API endpoints are authenticated or that user data is isolated.** Verify endpoint authorization, access controls, private data handling, rate limits, storage permissions, secrets, and TLS before allowing untrusted public users. See [AUDIT.md](AUDIT.md) for historical review notes; these do not replace a current security assessment.

Production templates exist in `deploy/Caddyfile` and `deploy/voice-studio.service`; adapt them to the actual host, permissions, domain, TLS, and process manager. Avoid public deployment until authentication and tenant isolation are verified.

## Licensing and attribution

Application code, third-party packages, and model checkpoints can have **different licenses and usage restrictions**. Review the [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [CosyVoice](https://github.com/FunAudioLLM/CosyVoice), and [Coqui XTTS-v2](https://huggingface.co/coqui/XTTS-v2) license terms before redistribution or commercial use. Confirm that this repository includes an explicit project license before describing the application itself as licensed under any particular terms.

## Additional references

- [Qwen3-TTS documentation](https://github.com/QwenLM/Qwen3-TTS)
- [CosyVoice documentation](https://github.com/FunAudioLLM/CosyVoice)
- [XTTS-v2 documentation](https://coqui-tts.readthedocs.io/en/latest/models/xtts.html)
- [Hardware/benchmark profiler guide](SYSTEM_PROFILE.md)
- [Repository audit notes](AUDIT.md)
