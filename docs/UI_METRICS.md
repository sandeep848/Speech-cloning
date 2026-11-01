# Comparison interface and signal diagnostics

The comparison workspace keeps synthesis, listening and numerical evaluation in separate tabs. Speech rate and temperature live in a collapsed Generation settings section. Metrics start with WER, speaker similarity, ScoreQ and RTFx; the focus selector exposes every existing measure and three new diagnostics. No prior measure was removed.

New evaluations calculate these from the generated audio, including the final speed adjustment:

- **Script pace (words/min):** Unicode script word tokens divided by total recording minutes, including pauses. This estimates delivery pace from the requested script; it does not verify spoken words or replace WER. Numbers, abbreviations and languages can affect tokenization.
- **Quiet frames (%):** percentage of audio samples in non-overlapping 20 ms windows with RMS below −40 dBFS. Channel power is pooled before thresholding; a final partial frame is weighted by its sample count. This includes pauses, quiet speech and leading/trailing silence; it is not a voice activity detector.
- **Near-clipping samples (%):** fraction of channel samples with absolute normalized amplitude at least 0.999. This is a signal warning indicator, not a perceptual distortion score or a true-peak measurement.

Diagnostics have no winner badge or invented quality thresholds. All-zero audio yields 100% quiet frames and 0% near clipping; empty audio produces no diagnostics. Non-finite samples produce an evaluation error instead of non-finite scores. Measurements stream through small audio blocks, without model downloads or additional dependencies. Floating sample/channel handling follows [python-soundfile](https://python-soundfile.readthedocs.io/en/latest/).

Previously saved evaluations show unavailable values for new diagnostics until recalculated; no historical scores are fabricated. Existing heuristic quality thresholds remain heuristics, not validated listening-study outcomes.
