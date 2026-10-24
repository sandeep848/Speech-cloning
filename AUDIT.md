# Speech pipeline audit — 24 October 2026

## Fixed

| Finding | Correction |
| --- | --- |
| Character-count duration estimate distributed all unused time across chunk joins | Removed duration forcing. Assembly preserves complete generated audio and adds only bounded joins. |
| Long outputs cut at the guessed sample count; sample count assumed 24 kHz | No clipping or padding to a duration target; each adapter's sample rate is retained. |
| Sentence and comma splitting repeatedly reset model context | Group short sentences up to 220 characters; preserve ordinary commas, decimal points and common abbreviations; bound long punctuation-free inputs at words. |
| Excessive model-generated quiet gaps survived edge trimming | Conservative 10 ms RMS detection caps sustained internal silence at 650 ms by default. Short pauses and speech boundaries are retained. |
| Fixed silence threshold could erase quiet speech and ignore final partial frames | Relative threshold, partial-frame handling and 40 ms edge margins. Silent, empty and non-finite chunks fail explicitly. |
| CosyVoice output pieces each received an artificial pause | Concatenate pieces of one inference first, then join text chunks. |
| CosyVoice replaced selected literal numbers with German words even for English | Preserve text content and punctuation for multilingual inference. |
| XTTS used overlapping short reference slices, discarding most reference material | Use the original reference with up to 30 seconds of conditioning in six-second chunks. Cache latents; restore upstream repetition penalty 10, keep speed 1.0. |
| Parallel jobs changed a process-wide language variable | Request-local ContextVar; reset on errors. |
| Catching any TypeError retried inference and hid decoder faults | Single adapter call; preserve the original error. |
| TTFT derived from total runtime and clamped to an arbitrary range | Remove invented TTFT; record observed time to first completed audio chunk under its own metric name. |
| Pitch proxy clamped to 1.5–8.5% and labeled FFE | Remove the invalid measurement. UI explains that FFE needs aligned recordings of the same utterance. |
| Generation cache reused recordings after pipeline/settings changes | Fixed pipeline revision in key plus inference settings; old outputs are not reused. |
| Tracked absolute model and evaluator symlinks depended on the original workstation | Remove symlinks; ignore runtime downloads; loaders retain configurable paths. |
| Presence of PyTorch alone marked CosyVoice configured | Require CosyVoice source/package presence. Loaded engines remain ready. |
| Launcher deleted files on startup and forced /var/tmp model paths | Stop automatic deletion; use a per-user configurable cache and remove injected private torch path. |
| Launcher reused stale frontend builds | Rebuild when source or frontend configuration is newer. |
| Backend CI installed every heavyweight model for mocked unit tests | Separate minimal test dependencies; production dependencies unchanged. |
| Documentation claimed exact word alignment and validated novelty | Replace unsupported claims with implemented behavior and explicit research proposals. |

## Validation

- Backend audio, adapter and API tests run without model downloads or a GPU.
- TypeScript checking and Vite production build.
- Python compilation and benchmark CLI argument check.
- Synthetic audio checks verify that excessive quiet gaps shrink, short pauses stay unchanged, speech samples survive assembly, and 22.05/24/48 kHz outputs retain their natural duration.
- Adapter mocks verify Qwen prompt reuse, XTTS conditioning and CosyVoice text preservation and contiguous internal pieces. These are integration-contract tests, not audible model validation.

## Remaining limitations

- GPU inference, real checkpoint compatibility and perceptual improvement have not been validated in the editing environment. No weights were trained or fine-tuned. Use `python backend/benchmark.py --language German --repetitions 3` on the deployment GPU and compare WAVs blind before claiming improvements.
- There is no universal quality ordering. Compare WER/CER, speaker similarity, quality and speed separately. Runtime sampling remains stochastic; the benchmark repeats trials and reports medians, not significance or guaranteed wins.
- Reference transcripts must accurately match reference speech. Automatic reference transcription/alignment and listening review are outside this fix.
- Quiet-gap detection is conservative, energy-based and has no word alignment; deliberate long dramatic pauses will be shortened too. Increase `MAX_INTERNAL_PAUSE_MS` if needed.
- Playback is not word-aligned. Natural outputs may have different durations. Mid-sentence switching between models does not guarantee identical spoken positions.
- ASR normalization, ScoreQ/DNSMOS domain calibration and speaker-embedding bias require a separate evaluation study. Quality estimator outputs are not human MOS ratings.
- First-audio timing is an in-memory completed-chunk observation, excludes model loading, and is unavailable after a server restart. It is not streaming token or browser playback latency.
- The upstream CosyVoice compatibility shims, dependency combinations and checkpoint downloads still require validation in the university GPU environment. Models and automatic evaluators can contend for GPU memory; benchmark synthesis sequentially.
