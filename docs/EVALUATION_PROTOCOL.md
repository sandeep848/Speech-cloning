# Evaluation protocol

No quality improvement is claimed by this implementation. The existing three-model benchmark remains available:

```bash
python backend/benchmark.py --language German --repetitions 3
```

Use a consented clean reference with a verbatim transcript. Freeze software/model/evaluator revisions and hardware. Include German and English, numbers, prices, abbreviations, technical vocabulary, questions, comma-separated clauses, paragraph changes and long passages. The existing benchmark has three scripts per language; it is a pilot corpus, not a comprehensive validated corpus.

Start with one-factor comparisons of supported temperature, rate, seed and pause settings, then pre-register a bounded factorial experiment. Exclude warmup, disable inference cache, randomize test order and retain failed trials. Record actual latency, duration, RTF, time to first completed chunk, WER/CER, ECAPA, ScoreQ and DNSMOS where available. Do not reintroduce FFE on unaligned utterances. Inspect ASR errors and quality-model domain limitations. Predicted quality is not human MOS.

For listening evaluation, assign anonymous recording labels and randomize order with a saved seed. Evaluate intelligibility, similarity, pronunciation, rhythm, punctuation and overall preference under consistent playback conditions. The existing UI still exposes model identities and the complete blinded/randomized listening workflow requested in the master prompt has NOT been implemented. Do not label current UI ratings as a completed blinded study. Existing per-user rating and agreement functionality is preserved.

Deliverables after genuine experiments: corpus manifest, trial configuration, audio catalog, objective metrics with missingness, listening instructions and anonymized responses, uncertainty estimates, reproducibility manifest and justified recommendations. No such empirical results were created in this environment.
