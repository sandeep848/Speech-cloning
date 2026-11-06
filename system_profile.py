"""Hardware, runtime configuration, and measured CPU/GPU TTS benchmarks.

Run: python system_profile.py
     python system_profile.py --benchmark --models qwen3 --devices cpu gpu
No model weights are loaded unless --benchmark is passed.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
MODEL_IDS = ("qwen3", "cosyvoice3", "xtts-v2")
SETTINGS = (
    "GENERATION_LANGUAGE", "MODEL_DEVICE", "QWEN_MODEL", "QWEN_MODEL_DIR",
    "QWEN_TEMPERATURE", "QWEN_TOP_P", "QWEN_TOP_K", "QWEN_SUBTALKER_TEMP",
    "QWEN_SUBTALKER_TOP_P", "QWEN_SPEECH_RATE", "QWEN_BATCH_SIZE",
    "COSY_MODEL_DIR", "COSY_HF_REPO", "COSY_SPEECH_RATE",
    "XTTS_MODEL", "XTTS_SPEECH_RATE", "XTTS_TEMPERATURE",
    "XTTS_REPETITION_PENALTY", "XTTS_SPEED", "SPEECH_RATE",
    "COMMA_PAUSE_MS", "SENTENCE_PAUSE_MS", "PARAGRAPH_PAUSE_MS",
    "CONTINUATION_PAUSE_MS", "SPLIT_ON_COMMA", "MAX_INTERNAL_PAUSE_MS",
    "QWEN_ATTN_IMPLEMENTATION", "HF_HOME", "TTS_HOME",
)
PACKAGE_NAMES = ("torch", "torchaudio", "qwen-tts", "coqui-tts",
                 "librosa", "soundfile", "transformers", "numpy")
DEFAULT_TEXT = "Hallo, heute testen wir die natürlich klingende Stimme. Sie spricht in normalem Tempo, mit verständlichen Pausen."
DEFAULTS = {
    "GENERATION_LANGUAGE": "German", "QWEN_SPEECH_RATE": "1.0",
    "COSY_SPEECH_RATE": "1.0", "XTTS_SPEECH_RATE": "1.0",
    "COMMA_PAUSE_MS": "120", "SENTENCE_PAUSE_MS": "260",
    "PARAGRAPH_PAUSE_MS": "450", "CONTINUATION_PAUSE_MS": "90",
    "SPLIT_ON_COMMA": "0", "MAX_INTERNAL_PAUSE_MS": "650",
    "QWEN_TEMPERATURE": "0.6", "QWEN_TOP_P": "0.85",
    "QWEN_TOP_K": "30", "QWEN_SUBTALKER_TEMP": "0.6",
    "QWEN_SUBTALKER_TOP_P": "0.85", "XTTS_TEMPERATURE": "0.75",
    "XTTS_REPETITION_PENALTY": "10.0", "XTTS_SPEED": "1.0",
}
# These are rough capacity-planning starting points, NOT measured minima.
CAPACITY_GUIDANCE = {
    "qwen3": {"gpu_vram_gb": "8-12+ (1.7B model)", "system_ram_gb": "16-32+",
              "cpu": "possible but potentially slow"},
    "cosyvoice3": {"gpu_vram_gb": "6-12+ (depends on precision/backend)",
                   "system_ram_gb": "16-32+", "cpu": "possible; validate dependencies"},
    "xtts-v2": {"gpu_vram_gb": "4-8+", "system_ram_gb": "8-16+",
                "cpu": "possible but potentially slow"},
}


def load_environment() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND / ".env", override=False)
    except ImportError:
        # Read-only reporting remains useful without optional dependencies.
        pass


def detect_hardware() -> dict:
    output = {"os": platform.platform(), "architecture": platform.machine(),
              "python": sys.version.split()[0], "executable": sys.executable,
              "logical_cpu_threads": os.cpu_count(), "cpu": platform.processor() or None,
              "ram_gb": None, "disk_free_gb": None, "torch": None,
              "cuda_available": False, "gpus": [], "notes": []}
    try:
        import psutil
        output["ram_gb"] = round(psutil.virtual_memory().total / 1024**3, 2)
        output["physical_cpu_cores"] = psutil.cpu_count(logical=False)
    except ImportError:
        output["notes"].append("Install psutil for accurate system RAM and physical core counts")
    try:
        output["disk_free_gb"] = round(shutil.disk_usage(ROOT).free / 1024**3, 2)
    except OSError:
        pass
    try:
        import torch
        output["torch"] = torch.__version__
        output["torch_cuda_build"] = torch.version.cuda
        output["cuda_available"] = bool(torch.cuda.is_available())
        if output["cuda_available"]:
            for index in range(torch.cuda.device_count()):
                prop = torch.cuda.get_device_properties(index)
                output["gpus"].append({
                    "index": index, "name": prop.name,
                    "vram_gb": round(prop.total_memory / 1024**3, 2),
                    "capability": list(torch.cuda.get_device_capability(index)),
                })
        elif torch.version.cuda:
            output["notes"].append("PyTorch has CUDA support, but no CUDA GPU is accessible")
        else:
            output["notes"].append("Installed PyTorch does not include CUDA support")
    except ImportError:
        output["notes"].append("PyTorch is not installed in this Python environment")
    return output


def collect_profile() -> dict:
    load_environment()
    versions = {}
    for package in PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "hardware": detect_hardware(),
            "packages": versions,
            "active_configuration": {key: os.environ.get(key, DEFAULTS.get(key, "(unset; auto/default)"))
                                     for key in SETTINGS},
            "configuration_sources": "Shell environment overrides backend/.env; missing entries show known code defaults or auto/default.",
            "planning_guidance_not_verified_minima": CAPACITY_GUIDANCE,
            "requirements_note": "Model weight size, precision, sentence length, and runtime versions change RAM/VRAM usage. Test on target hardware.",
            "benchmarks": [], "comparison": {}}


def run_worker(args) -> int:
    # Each model/device pair runs in a fresh interpreter to avoid cached
    # model/device state contaminating the timing comparison.
    os.environ["MODEL_DEVICE"] = "cpu" if args.worker_device == "cpu" else "cuda:0"
    load_environment()
    result = {"model": args.worker_model, "device": args.worker_device,
              "status": "error", "load_seconds": None, "warmup_seconds": None,
              "trials": [], "median_seconds": None, "median_rtf": None,
              "peak_gpu_allocated_gb": None}
    try:
        import statistics
        import torch
        import soundfile as sf
        if args.worker_device == "gpu" and not torch.cuda.is_available():
            raise RuntimeError("No CUDA GPU available to this PyTorch environment")
        sys.path.insert(0, str(BACKEND))
        from models import get_engine, generate_audio
        reference = Path(args.reference)
        transcript_path = Path(args.transcript)
        if not reference.is_file() or not transcript_path.is_file():
            raise FileNotFoundError("Reference WAV and transcript TXT are required")
        transcript = transcript_path.read_text(encoding="utf-8").strip()
        if not transcript:
            raise ValueError("Reference transcript is empty")
        def sync():
            if args.worker_device == "gpu":
                torch.cuda.synchronize()
        with tempfile.TemporaryDirectory(prefix="tts-profile-") as td:
            output = Path(td) / "generated.wav"
            sync()
            started = time.perf_counter()
            get_engine(args.worker_model)
            sync()
            result["load_seconds"] = round(time.perf_counter() - started, 4)
            started = time.perf_counter()
            generate_audio(args.worker_model, args.text, reference, transcript, output)
            sync()
            result["warmup_seconds"] = round(time.perf_counter() - started, 4)
            for _ in range(args.repetitions):
                sync()
                started = time.perf_counter()
                generate_audio(args.worker_model, args.text, reference, transcript, output)
                sync()
                elapsed = time.perf_counter() - started
                duration = float(sf.info(output).duration)
                result["trials"].append({
                    "elapsed_seconds": round(elapsed, 4),
                    "audio_seconds": round(duration, 4),
                    "rtf": round(elapsed / duration, 4) if duration > 0 else None,
                })
        result["median_seconds"] = round(statistics.median(
            t["elapsed_seconds"] for t in result["trials"]), 4)
        valid = [t["rtf"] for t in result["trials"] if t["rtf"] is not None]
        result["median_rtf"] = round(statistics.median(valid), 4) if valid else None
        if args.worker_device == "gpu":
            result["peak_gpu_allocated_gb"] = round(
                torch.cuda.max_memory_allocated() / 1024**3, 3)
        result["status"] = "ok"
    except Exception as exc:
        result["error"] = str(exc)
    Path(args.worker_output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["status"] == "ok" else 1


def save_report(path: Path, profile: dict) -> None:
    """Preserve prior reports if a quota or filesystem error interrupts saving."""
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".system-profile-", suffix=".tmp", delete=False
        ) as out:
            temp_name = out.name
            json.dump(profile, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true",
                        help="Run actual inference (may download weights; can take substantial time)")
    parser.add_argument("--models", nargs="+", choices=MODEL_IDS, default=list(MODEL_IDS))
    parser.add_argument("--devices", nargs="+", choices=("cpu", "gpu"), default=["cpu", "gpu"])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--reference", default=str(BACKEND / "reference/reference.wav"))
    parser.add_argument("--transcript", default=str(BACKEND / "reference/reference.txt"))
    parser.add_argument("--output", type=Path, default=ROOT / "system-profile.json")
    # Internal worker flags: isolate models to compare fresh processes.
    parser.add_argument("--worker-model", choices=MODEL_IDS, help=argparse.SUPPRESS)
    parser.add_argument("--worker-device", choices=("cpu", "gpu"), help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be >= 1")
    if not args.text.strip():
        parser.error("--text must not be empty")
    if args.worker_model:
        if not args.worker_output or not args.worker_device:
            parser.error("Worker invocation is incomplete")
        return run_worker(args)
    profile = collect_profile()
    if args.benchmark:
        if not Path(args.reference).is_file() or not Path(args.transcript).is_file():
            parser.error("Benchmark requires a reference WAV and transcript TXT")
        for model in args.models:
            for device in dict.fromkeys(args.devices):
                if device == "gpu" and not profile["hardware"]["cuda_available"]:
                    profile["benchmarks"].append({
                        "model": model, "device": device, "status": "unavailable",
                        "reason": "CUDA GPU not detected; no benchmark was run"})
                    continue
                with tempfile.TemporaryDirectory(prefix="tts-worker-") as td:
                    result_path = Path(td) / "result.json"
                    command = [sys.executable, str(Path(__file__).resolve()),
                               "--worker-model", model, "--worker-device", device,
                               "--worker-output", str(result_path), "--reference", args.reference,
                               "--transcript", args.transcript, "--text", args.text,
                               "--repetitions", str(args.repetitions)]
                    print(f"Benchmarking {model} on {device}...", file=sys.stderr, flush=True)
                    proc = subprocess.run(command, cwd=ROOT, check=False)
                    if result_path.is_file():
                        profile["benchmarks"].append(json.loads(result_path.read_text(encoding="utf-8")))
                    else:
                        profile["benchmarks"].append({
                            "model": model, "device": device, "status": "error",
                            "error": f"Worker exited with code {proc.returncode} without results"})
                # Persist each result, but do not truncate a previous report on failure.
                try:
                    save_report(args.output, profile)
                except OSError as exc:
                    print(f"Unable to save report: {exc}", file=sys.stderr)
                    print("Stopping benchmarks to avoid further model downloads.", file=sys.stderr)
                    return 1
                if profile["benchmarks"][-1].get("status") == "error":
                    error_text = profile["benchmarks"][-1].get("error", "").lower()
                    if "disk quota exceeded" in error_text or "no space left on device" in error_text:
                        print("Disk storage exhausted; stopping remaining benchmarks.", file=sys.stderr)
                        return 1
        for model in args.models:
            rows = {r["device"]: r for r in profile["benchmarks"]
                    if r["model"] == model and r["status"] == "ok"}
            if "cpu" in rows and "gpu" in rows:
                cpu = rows["cpu"]["median_seconds"]
                gpu = rows["gpu"]["median_seconds"]
                if gpu and cpu:
                    profile["comparison"][model] = {
                        "cpu_median_seconds": cpu, "gpu_median_seconds": gpu,
                        "gpu_speedup": round(cpu / gpu, 2),
                        "meaning": "Greater than 1 means GPU inference was faster"}
            else:
                profile["comparison"][model] = "Not measured on both CPU and GPU"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_report(args.output, profile)
    print(f"Report: {args.output}")
    print("Hardware:", json.dumps(profile["hardware"], indent=2))
    if args.benchmark:
        for row in profile["benchmarks"]:
            print(row["model"], row["device"], row["status"],
                  "median_s=", row.get("median_seconds"),
                  "RTF=", row.get("median_rtf"))
        print("CPU/GPU comparison:", json.dumps(profile["comparison"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
