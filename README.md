# Voice Comparison Studio

A unified, single-environment research and benchmarking platform for state-of-the-art neural voice cloning. Run **Qwen3-TTS (1.7B)**, **CosyVoice 3 (0.5B)**, and **Coqui XTTS-v2** simultaneously side-by-side in one process, with comprehensive automated acoustic, intelligibility, speaker similarity, and latency evaluations.

---

## Highlights & Novel Research Contributions

Voice Comparison Studio incorporates novel architectural protocols designed for cross-architecture neural TTS benchmarking (see [NOVELTY_AND_ARCHITECTURAL_SUGGESTIONS.md](NOVELTY_AND_ARCHITECTURAL_SUGGESTIONS.md)):

1. **Iso-Duration Speech Synthesis & Temporal Alignment Protocol (CATAS)**:
   - Synchronizes speech duration across fundamentally different TTS architectures (autoregressive, flow-matching, diffusion).
   - Uses silence-energy RMS thresholding and pause distribution to achieve sample-exact duration parity without phase-vocoder distortion.
2. **Live Mid-Playback Cross-Fader & Zero-Drift Solo Switcher**:
   - Seamlessly switch or sweep between Ground-Truth Reference, Qwen3-TTS, CosyVoice 3, and XTTS-v2 mid-sentence in real time.
   - Eliminates human auditory echoic memory decay during A/B comparative listening.
3. **Five-Axis Pareto Frontier Spider Matrix & Composite Voice Performance Index (VPI)**:
   - Holistically evaluates models on a 0–100 scale balancing Acoustic Naturalness (ScoreQ/DNSMOS), Speaker Similarity (ECAPA), Phonetic Accuracy ($1 - \text{WER}$), Computational Throughput ($\text{RTFx}$), and Prosodic Inflection ($1 - \text{FFE}$).
4. **Interactive Fundamental Frequency ($F_0$) & Prosody Contour Overlay**:
   - Instantaneous pitch extraction ($65\text{Hz} - 360\text{Hz}$) using `librosa.yin` across 10ms frames.
   - Overlays candidate engine pitch trajectories against the target speaker anchor line to detect monotone drift and pitch octave errors.
5. **Token-Level Phonetic Alignment & Hallucination Diagnostics**:
   - Word-level diff and confidence inspection pinpointing exact timestamps of pronunciation drift or hallucinated tokens.
6. **Subjective Blind MOS / ABX Listening Suite**:
   - Double-blind randomized evaluation with inter-rater agreement computation (Cohen's Kappa).

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
│   ├── evaluator.py                        # Automated multi-metric evaluation suite (WER, CER, ECAPA, ScoreQ, DNSMOS, FFE)
│   ├── requirements.txt                    # Python dependencies
│   ├── .env.example                        # Configuration template
│   ├── models/                             # Unified model manager & engine adapters
│   │   ├── __init__.py                     # Model registry, device auto-selection, CATAS waveform assembly
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
| **CosyVoice 3** | 0.5B | Multi-Stage Flow Matching + LLM Backbone | Ultra-fast real-time factor ($\text{RTF} < 0.3$), highly stable pitch contour. |
| **Coqui XTTS-v2** | Multilingual | Autoregressive + HiFi-GAN Vocoder | Rapid Time-to-First-Token ($\text{TTFT}$), robust multilingual cross-lingual cloning. |

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
  - **$F_0$ Frame Error Rate (FFE)**: librosa-YIN fundamental frequency tracking capturing gross pitch error and jitter.
- **Computational Throughput & Latency**:
  - **Generation Latency ($s$)**: End-to-end model execution time.
  - **Real-Time Factor (RTF)**: Generation seconds divided by audio duration seconds.
  - **Inverse RTF ($\text{RTFx}$)**: Speedup over real-time playback ($\text{target} > 1.25\times$).
  - **Time-to-First-Token ($\text{TTFT}$)**: Estimated response latency for conversational applications.

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
