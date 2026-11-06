# Hardware and CPU/GPU performance report

Run from the repository root in the SAME Python environment you use to start the backend.

```bash
conda activate qwen3
python system_profile.py
```

This writes `system-profile.json`. The default command **does not download model weights or run inference**. It reports CPU threads, available RAM information (requires optional `psutil`), disk space, PyTorch CUDA compatibility, GPU model/VRAM, dependency versions, and speech-generation environment settings. Do not post the report publicly without reviewing paths and environment details.

To perform genuine inference measurements:

```bash
python system_profile.py --benchmark --models qwen3 cosyvoice3 xtts-v2 --devices cpu gpu --repetitions 3
```

You can benchmark one model at a time, e.g.:

```bash
python system_profile.py --benchmark --models qwen3 --devices cpu --repetitions 3
```

The optional benchmark requires `backend/reference/reference.wav` and a corresponding accurate `backend/reference/reference.txt`. The benchmark uses existing model adapters; it may download large weights, take time, and consume significant RAM/VRAM. Workloads are identical between CPU and GPU. Model loads occur in separate child processes; loading time is distinguished from warmup and repeated inference. CUDA is synchronized before and after GPU timings. The reported real-time factor (RTF) is inference seconds divided by audio seconds. **RTF < 1** indicates generation faster than the playback duration. GPU speedup is computed only if real CPU and GPU inference runs both succeed. The report never substitutes a guessed GPU measurement.

Hardware ranges in the output are provisional capacity-planning guides, not proven minimum requirements. CPU/GPU comparisons are meaningful only with the same model versions, reference clip, text, and host constraints. For robust conclusions, conduct multiple independent runs with representative longer inputs, monitor host memory, and listen to the generated output. Avoid concurrent inference and other heavy GPU tasks during measurement.

On university machines with no CUDA device, `gpu` results are marked *unavailable*, not benchmarked. The report exposes the Python executable to help detect the wrong conda interpreter.

To install optional hardware introspection:

```bash
python -m pip install psutil
```
