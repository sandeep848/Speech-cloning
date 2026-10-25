# Voice Comparison Studio

A unified, single-environment research and benchmarking platform for state-of-the-art neural voice cloning. Run **Qwen3-TTS (1.7B)**, **CosyVoice 3 (0.5B)**, and **Coqui XTTS-v2** simultaneously side-by-side in one process, with comprehensive automated acoustic, intelligibility, speaker similarity, and latency evaluations.

---

## Speech quality and timing

- Outputs retain their generated length. They are never padded to a guessed speaking rate or truncated to make models match.
- Speech is phrased at commas and sentence endings while retaining punctuation. Blank lines remain paragraph breaks. Decimal/thousands separators, titles and initials do not become artificial boundaries. Long phrases still split at bounded word boundaries.
- Defaults are 150 ms at commas/semicolons/colons, 320 ms at sentence endings, 600 ms between paragraphs and 90 ms at long-phrase continuation joins. Retained model silence counts toward each pause, so gaps are not doubled.
- Qwen speech is gently slowed to 0.92x; CosyVoice and XTTS to 0.96x. Pitch-preserving time stretching affects speech chunks only; it does not change the WAV sample rate or stretch inserted punctuation gaps.
- Very quiet internal gaps longer than 650 ms are shortened conservatively. Set `MAX_INTERNAL_PAUSE_MS` between 150 and 3000 to change this limit.
- Qwen caches its reference clone prompt. CosyVoice caches its zero-shot speaker and preserves multilingual text. XTTS caches conditioning from up to 30 seconds of reference with six-second chunks and uses its upstream repetition penalty of 10.
- Language is isolated per request, including concurrent German and English jobs.
- First audio latency is measured when an adapter returns its first completed chunk. It is not token latency or browser playback latency. True FFE is unavailable without aligned recordings of the same utterance.

See [AUDIT.md](AUDIT.md) for fixes, validation and remaining limitations. No model is guaranteed to beat another: quality, identity, accuracy and speed can have different winners.

### Tune pacing

These defaults work without editing `.env`. To override them, add the following to `backend/.env`, restart the server and generate new audio:

```ini
QWEN_SPEECH_RATE=0.92
COSY_SPEECH_RATE=0.96
XTTS_SPEECH_RATE=0.96
COMMA_PAUSE_MS=150
SENTENCE_PAUSE_MS=320
PARAGRAPH_PAUSE_MS=600
CONTINUATION_PAUSE_MS=90
```

Rates are relative to model output: `1.0` preserves native speed. A common `SPEECH_RATE` can set all engines unless a model-specific rate overrides it. `XTTS_SPEED` remains its separate native inference control; keep it at `1.0` when using the shared adjustment to avoid compounding slowdowns. Blank lines create paragraph breaks; single wrapped line breaks remain inside a paragraph. Pause/rate settings are included in the generation cache key.

These are configurable pacing defaults, not a guarantee of human-like speech. Clause-level synthesis can add inference overhead and reset prosody at boundaries, especially for very short phrases. Gentle time stretching can also introduce artifacts; compare actual model output and set the model-specific rate to `1.0` if preferred.

## Measure the models on your GPU

After installing dependencies, run from the repository root:

```bash
python backend/benchmark.py --language German --repetitions 3
```

This runs an excluded warmup and three uncached trials per script and model, sequentially to reduce GPU contention. It saves generated WAVs, measured metrics, unavailable-metric errors and per-model medians under `backend/data/benchmarks/`. Listen blind as well as comparing WER/CER, ECAPA, quality scores and RTF. Use the same reference/transcript, hardware and settings for before/after runs. Numbers are ASR-sensitive; inspect transcripts when interpreting WER. English trials require an appropriate reference via `--reference` and `--transcript`.

---

## Architecture Overview

```
Speech-cloning/
├── start.py                                # Unified launcher: checks environment, preloads models & evaluators, launches API & UI
├── NOVELTY_AND_ARCHITECTURAL_SUGGESTIONS.md # Detailed specification of novel research contributions
├── AUDIT.md                                # Security, concurrency, and architecture audit history
├── backend/
│   ├── backend.py                          # Consolidated FastAPI service, SQLite persistence, and endpoints
│   ├── direct_inference.py                 # Direct in-process synthesis interface
│   ├── doctor.py                           # Non-destructive diagnostics for models, devices, and dependencies
│   ├── evaluator.py                        # Automated multi-metric evaluation suite (WER, CER, ECAPA, ScoreQ, DNSMOS)
│   ├── requirements.txt                    # Python dependencies
│   ├── .env.example                        # Configuration template
│   ├── models/                             # Unified model manager & engine adapters
│   │   ├── __init__.py                     # Model registry, device auto-selection, natural waveform assembly
│   │   ├── qwen3/                          # Qwen3-TTS 12Hz 1.7B Base loader, prompt caching, synthesis
│   │   ├── cosyvoice3/                     # CosyVoice 3 0.5B loader, runtime shims, zero-shot synthesis
│   │   ├── xtts_v2/                        # Coqui XTTS-v2 loader, voice clone conditioning, synthesis
│   │   └── evaluators/                     # Evaluator checkpoints, ONNX models, and caches
│   ├── reference/                          # Reference speech recordings (.wav) and transcripts (.txt)
│   │   ├── reference.wav                   # Primary reference audio
│   │   ├── reference.txt                   # Primary reference transcript
│   │   ├── reference_1.wav                 # Alternate reference audio
│   │   └── reference_1.txt                 # Alternate reference transcript
│   └── tests/
│       └── test_api.py                     # API, authentication, concurrency, and evaluation regression tests
├── frontend/                               # Modern React 18 + TypeScript + Vite UI
│   ├── src/
│   │   ├── main.tsx                        # Main UI with interactive cross-fader, radar matrix, pitch overlay, blind MOS
│   │   ├── style.css                       # Dark theme responsive styling
│   │   └── vite-env.d.ts                   # TypeScript Vite definitions
│   ├── index.html                          # Single-page application entry point
│   ├── package.json                        # Frontend dependencies & build scripts
│   ├── tsconfig.json                       # TypeScript compiler configuration
│   └── vite.config.ts                      # Vite configuration & dev proxy
├── deploy/                                 # Production deployment templates
│   ├── Caddyfile                           # Reverse proxy with automatic HTTPS
│   └── voice-studio.service                # Systemd service unit template
└── .github/workflows/
    ├── ci.yml                              # Automated backend tests and frontend production build verification
    └── deploy.yml                          # Optional manual GitHub Pages deployment
```

---

## Supported Neural TTS Models

| Engine | Parameter Size | Architecture | Strengths |
| :--- | :--- | :--- | :--- |
| **Qwen3-TTS** | 1.7B | Autoregressive + 12Hz Continuous Audio Codec | Exceptional naturalness, deep prosodic fidelity, strong zero-shot speaker capture. |
| **CosyVoice 3** | 0.5B | Multi-Stage Flow Matching + LLM Backbone | Zero-shot multilingual synthesis; benchmark speed on your hardware. |
| **Coqui XTTS-v2** | Multilingual | Autoregressive + HiFi-GAN Vocoder | Multilingual cloning with cached speaker conditioning. |

---

## Automated Multi-Metric Evaluation Suite

All generated recordings are automatically benchmarked against the target reference voice:

- **Speech Intelligibility & Accuracy**:
  - **Word Error Rate (WER)**: Whisper transcription accuracy normalized via `jiwer`.
  - **Character Error Rate (CER)**: Fine-grained character/phoneme substitution tracking.
- **Speaker Identity Replication**:
  - **ECAPA-TDNN Similarity**: SpeechBrain ECAPA cosine similarity score ($\text{target} > 0.80$).
- **Acoustic Quality & Naturalness**:
  - **ScoreQ Quality Score**: Non-intrusive neural Mean Opinion Score (1.00 – 5.00).
  - **Microsoft DNSMOS P.835**: Speech quality (SIG), background noise distortion (BAK), and overall quality (OVRL).
- **Pitch & Inflection Accuracy**:
  - **FFE**: Unavailable for unaligned reference and generated speech; no substitute score is fabricated.
- **Computational Throughput & Latency**:
  - **Generation Latency ($s$)**: End-to-end model execution time.
  - **Real-Time Factor (RTF)**: Generation seconds divided by audio duration seconds.
  - **Inverse RTF ($\text{RTFx}$)**: Speedup over real-time playback ($\text{target} > 1.25\times$).
  - **Time to First Audio Chunk**: Observed completion time of the first generated chunk, excluding model loading.

---

## Quick Start

### 1. Prerequisites
- **Python**: 3.10 – 3.13 (Python 3.12 recommended)
- **Node.js**: v20+ and npm
- **GPU**: NVIDIA GPU with CUDA recommended for real-time inference (CPU execution fallback supported)

### 2. Single-Step Launch

The studio provides an all-in-one launcher `start.py` that verifies dependencies, preloads models and evaluators into memory, builds the frontend if needed, and starts the studio at `http://127.0.0.1:8000`:

```bash
python start.py
```

Options:
- `--port <PORT>`: Specify listening port (default: `8000`).
- `--no-browser`: Do not automatically open the default web browser.
- `--verify-only`: Preload models, run a test synthesis and metric evaluation pass, print results, and exit.

### 3. Manual Step-by-Step Setup (Alternative)

```bash
# 1. Install Python dependencies
python -m pip install -r backend/requirements.txt

# 2. Build the React frontend
cd frontend
npm ci
npm run build
cd ..

# 3. Start the FastAPI server
python -m uvicorn backend.backend:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** in your browser.

---

## Diagnostics & Verification

### Run Doctor Diagnostics
To verify Python packages, CUDA GPU availability, and model readiness:
```bash
python backend/doctor.py
```

### Run Automated Backend Tests
```bash
pytest backend/tests -v
```

### Build & Verify Frontend
```bash
cd frontend
npm ci
npm run build
npm audit --audit-level=moderate
```

---

## Production Deployment

A production deployment runs the FastAPI server behind a reverse proxy (e.g., Caddy or Nginx) handling HTTPS:

1. Copy and adjust `backend/.env.example` to `backend/.env`.
2. Configure authentication tokens:
   - `API_TOKEN`: Secret for general studio access.
   - `ADMIN_TOKEN`: Secret for administrative actions (e.g., reloading reference voice).
3. Use the systemd template in `deploy/voice-studio.service` for managed process lifecycle.
4. Adapt `deploy/Caddyfile` for automated TLS certificate provisioning and secure HTTP headers.

---

## License

This project is licensed under the Apache License 2.0. Model checkpoints are subject to their respective upstream licenses (Qwen License, FunAudioLLM License, Coqui Public Model License).
