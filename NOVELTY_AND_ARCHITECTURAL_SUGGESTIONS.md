# Voice Comparison Studio: Novel Architectural Proposals & Research Contributions

This document outlines the proposed novel features, architectural contributions, and benchmarking methodologies for **Voice Comparison Studio**. These innovations elevate the project from a standard text-to-speech demo into a publication- and industry-grade research platform for cross-paradigm neural voice cloning.

---

## 1. Iso-Duration Speech Synthesis & Temporal Alignment Protocol (CATAS)

### Problem Statement
In traditional speech evaluation, benchmarking disparate neural TTS architectures (e.g., Autoregressive vs. Flow Matching vs. Multi-Stage Diffusion) is inherently confounded by mismatched temporal lengths, disparate speech rates, and unaligned silence distributions. This variance invalidates direct, sample-synchronous A/B/X subjective listening tests and introduces acoustic bias into differential spectral evaluations.

### Novel Solution: Cross-Architecture Temporal Alignment Synthesis (CATAS)
- **Deterministic Script Cadence Modeling**: Computes an exact, target sample duration $S_{\text{target}}$ based on language-specific conversational speech rates and clause boundary analysis.
- **Silence-Energy Trimming & Waveform Assembly**: Autonomously strips vocoder tail artifacts and breathes using RMS energy windowing with adaptive thresholding ($thresh = 0.005$, $25\text{ms}$ windows).
- **Even Pause Distribution**: Computes the exact residual silence:
  $$\Delta_{\text{silence}} = S_{\text{target}} - \sum_{i=1}^K S_{\text{speech}, i}$$
  and distributes it proportionally across head padding, tail padding, and inter-sentence pauses.
- **Outcome**: All three architectures produce audio with **byte-for-byte and millisecond-exact duration parity** without phase-vocoder time-stretching or spectral degradation.

---

## 2. Live Mid-Playback Cross-Fader (Zero-Drift Audio Morphing)

### Problem Statement
Human acoustic short-term memory (echoic memory) degrades rapidly within 3 to 4 seconds. When evaluators listen to Model A, pause, and then listen to Model B, their auditory cortex struggles to isolate subtle differences in formant definition, spectral clarity, and vocoder distortion.

### Novel Solution
Leveraging the sample-exact temporal alignment achieved by CATAS, the studio introduces a **real-time seamless cross-fader and instant solo switcher**:
- **Continuous Scrubbing**: While the synthesized voice is speaking (e.g., midway through a sentence), the user clicks between **Ground-Truth Reference**, **Qwen3-TTS**, **CosyVoice 3**, and **XTTS-v2** (or sweeps a cross-fader slider).
- **Phase-Preserving Transition**: The playback head transitions instantly at the identical audio sample without pausing, seeking, or tempo discontinuity.
- **Why It Is Novel**: No existing TTS benchmark or evaluation platform offers continuous, live mid-speech cross-fading across distinct neural TTS models because standard systems produce mismatched temporal lengths.

---

## 3. Five-Axis Pareto Frontier Spider Matrix & Composite Voice Performance Index (VPI)

### Problem Statement
Modern neural TTS models are evaluated on isolated, conflicting metrics. For example:
- Qwen3-TTS achieves superior MOS naturalness, but incurs higher computational latency.
- CosyVoice 3 delivers ultra-low RTF and flow-matching efficiency, but distinct timbre characteristics.
- XTTS-v2 offers rapid Time-to-First-Token (TTFT), but requires careful reference conditioning.

Currently, there is no standardized, holistic metric that captures this multidimensional trade-off.

### Novel Solution: Composite Voice Performance Index (VPI)
A weighted, normalized 0–100 benchmark metric:
$$\text{VPI} = 100 \times \left( w_1 \cdot \frac{\text{ScoreQ}}{5.0} + w_2 \cdot \frac{\text{DNSMOS}_{\text{OVRL}}}{5.0} + w_3 \cdot \text{ECAPA} + w_4 \cdot (1 - \text{WER}) + w_5 \cdot \min\left(1.0, \frac{\text{RTFx}}{2.0}\right) - w_6 \cdot \text{FFE} \right)$$

### 5-Axis Spider / Radar Dimension Matrix
1. **Acoustic Naturalness**: ScoreQ neural MOS ($> 4.00$) and DNSMOS P.835 ($> 3.60$).
2. **Speaker Identity Replication**: SpeechBrain ECAPA-TDNN cosine similarity ($> 0.80$).
3. **Phonetic Intelligibility**: Whisper-normalized transcription accuracy ($1 - \text{WER} > 97\%$).
4. **Computational Throughput**: Inverse Real-Time Factor ($\text{RTFx} > 1.0\times$ real-time).
5. **Prosodic Inflection Fidelity**: Fundamental frequency tracking ($1 - \text{FFE} > 95\%$).

---

## 4. Interactive Fundamental Frequency ($F_0$) & Prosody Contour Overlay

### Problem Statement
Scalar metrics such as F0 Frame Error Rate (FFE) output a single percentage, but fail to explain *where* and *why* an engine sounds unnatural or robotic.

### Novel Solution
An interactive SVG/Canvas pitch curve visualizer directly on the frontend:
- **Instantaneous Pitch Extraction**: Computes instantaneous pitch tracks via `librosa.yin` ($65\text{Hz} - 360\text{Hz}$) across $10\text{ms}$ frames.
- **Prosody Trajectory Comparison**: Plots the target speaker's natural pitch trajectory as an anchor line, overlaying the generated pitch curves of all models.
- **Visual Anomaly Highlighting**: Visually highlights monotone drift, unnatural pitch resets, and erratic octave jumps directly on the timeline.

---

## 5. Token-Level Phonetic Alignment & Hallucination Diagnostics

### Problem Statement
Standard WER and CER collapse entire sentences into a single aggregate error figure, obscuring subtle pronunciation quirks, dropped phonemes, or vocoder slurring.

### Novel Solution
A visual, word-level ASR confidence and diff inspector:
- **Phonetic Status Color-Coding**:
  - 🟢 **Green**: Accurate phoneme match with high ASR confidence.
  - 🟡 **Amber**: Accent / minor dialectal substitution (e.g., German umlaut or compound word split).
  - 🔴 **Red**: Syllable truncation, word omission, or hallucinated token.
- **Error Attribution**: Pinpoints the exact timestamp where synthesis fidelity degraded.

---

## Summary Matrix of Contributions

| Feature | Research / Industry Precedent | Voice Comparison Studio Innovation |
| :--- | :--- | :--- |
| **Duration Matching** | Variable-length outputs or phase-vocoder stretching (distorts audio) | **CATAS**: Zero-distortion pause-distributed temporal synchronization |
| **A/B Listening** | Sequential listening with echoic memory decay | **Live Synchronized Cross-Fader**: Mid-sentence, real-time model switching |
| **Multi-Engine Evaluation** | Disconnected scalar metrics (WER, RTF) | **5-Axis Pareto Radar Matrix & Composite VPI (0–100 score)** |
| **Prosody Assessment** | Aggregate F0 error scalars | **Interactive Dynamic Pitch Contour Overlay** |
| **Diagnostics** | Plain text diff or scalar WER | **Token-level Phonetic Alignment & Hallucination Inspector** |
