# Research directions

These are proposals, not implemented or validated research contributions.

- Forced alignment for the same text could support word-synchronous A/B playback. Adding silence to equalize total duration does not align words and biases RTF.
- True pitch frame error needs matched utterances, voiced/unvoiced decisions and temporal alignment. Comparing generated pitch with the median pitch of a different sentence is not FFE.
- Streaming first-audio and first-token timings require separate instrumentation. Batch completion timings do not establish streaming latency.
- Quality comparisons should report repeated uncached trials, hardware, settings, missing metrics and blind listening results. No universal model ordering is assumed.

The implemented natural-duration pipeline and reproducible benchmark are documented in README.md and AUDIT.md.
