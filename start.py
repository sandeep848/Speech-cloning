"""Single-environment launcher for Voice Comparison Studio.

Ensures all dependencies and model weights are present in backend/models/,
removes obsolete multi-env files, preloads all 3 TTS models and all 3 metric
evaluators in one process, and starts the studio on http://127.0.0.1:8000.
"""

from __future__ import annotations
import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"

REQUIRED_IMPORTS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "dotenv": "python-dotenv",
    "soundfile": "soundfile",
    "scipy": "scipy",
    "librosa": "librosa",
    "einops": "einops",
    "inflect": "inflect",
    "omegaconf": "omegaconf",
    "hyperpyyaml": "HyperPyYAML",
    "huggingface_hub": "huggingface_hub",
    "onnxruntime": "onnxruntime",
    "qwen_tts": "qwen-tts",
    "TTS": "coqui-tts",
    "whisper": "openai-whisper",
    "jiwer": "jiwer",
    "speechbrain": "speechbrain",
    "scoreq": "scoreq",
}


def ensure_single_environment() -> None:
    """If launched from a base Python without TTS packages, re-exec into the unified conda env if present."""
    if (
        importlib.util.find_spec("qwen_tts") is not None
        and importlib.util.find_spec("TTS") is not None
    ):
        return
    candidates = [
        Path.home() / "anaconda3/envs/qwen3/bin/python",
        Path.home() / "miniconda3/envs/qwen3/bin/python",
        Path.home() / "anaconda3/envs/qwen3/python.exe",
        Path.home() / "miniconda3/envs/qwen3/python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() != Path(sys.executable).resolve():
            os.execv(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])


def ensure_dependencies_installed() -> None:
    """On first-time deployment on a new machine, install any missing Python packages."""
    missing = [
        pkg
        for mod, pkg in REQUIRED_IMPORTS.items()
        if importlib.util.find_spec(mod) is None
    ]
    if missing:
        print(f"[setup] Installing missing packages in {sys.executable}: {', '.join(missing)}", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            check=True,
        )


def build_frontend() -> None:
    built = ROOT / "frontend/dist/index.html"
    sources = [* (ROOT / "frontend/src").rglob("*"),
               ROOT / "frontend/package.json", ROOT / "frontend/package-lock.json",
               ROOT / "frontend/vite.config.ts", ROOT / "frontend/index.html"]
    if built.is_file() and all(
        not path.is_file() or path.stat().st_mtime <= built.stat().st_mtime
        for path in sources
    ):
        return
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("Frontend build missing and npm not found.")
    for command in ("ci", "run build"):
        subprocess.run([npm, *command.split()], cwd=ROOT / "frontend", check=True)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def open_browser_when_ready(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(1)


def main() -> int:
    # Respect configured paths on Linux, Windows and shared university hosts.
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND / ".env", override=False)
    except ImportError:
        pass
    local_storage_base = Path(os.getenv(
        "VOICE_STUDIO_MODEL_CACHE", str(Path.home() / ".cache/voice-studio")
    )).expanduser()
    for subdir in ("hf", "tts", "cosyvoice3", "qwen3"):
        (local_storage_base / subdir).mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(local_storage_base / "hf"))
    os.environ.setdefault("TTS_HOME", str(local_storage_base / "tts"))
    os.environ.setdefault("COSY_MODEL_DIR", str(local_storage_base / "cosyvoice3"))
    os.environ.setdefault("QWEN_MODEL_DIR", str(local_storage_base / "qwen3"))
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
    os.environ.setdefault("GENERATION_LANGUAGE", "German")
    os.environ.setdefault("ENABLE_WHISPER", "1")
    os.environ.setdefault("ENABLE_ECAPA", "1")
    os.environ.setdefault("ENABLE_SCOREQ", "1")
    os.environ["USE_TF"] = "0"
    os.environ["USE_FLAX"] = "0"
    os.environ["PYTHONUNBUFFERED"] = "1"

    ensure_single_environment()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--verify-only", action="store_true", help="Preload all models/metrics and run a full synthesis + evaluation check, then exit.")
    args = parser.parse_args()

    ensure_dependencies_installed()
    build_frontend()

    if not args.verify_only and not port_free(args.port):
        raise RuntimeError(f"Port {args.port} is already in use. Stop the existing server and retry.")

    sys.path.insert(0, str(BACKEND))
    import direct_inference
    import backend as studio_backend

    print(f"Using unified Python environment: {sys.executable}", flush=True)
    print("Preparing and loading all TTS models and metric evaluators...", flush=True)
    studio_backend._startup_preload()
    os.environ["PRELOAD_MODELS"] = "0"  # Already preloaded in this process

    if args.verify_only:
        import tempfile

        ref = BACKEND / "reference/reference.wav"
        txt = (BACKEND / "reference/reference.txt").read_text(encoding="utf-8").strip()
        evaluator = studio_backend.get_evaluator()
        with tempfile.TemporaryDirectory(prefix="studio-verify-") as tmp:
            for model_id in studio_backend.MODEL_IDS:
                out = Path(tmp) / f"{model_id}.wav"
                t0 = time.perf_counter()
                direct_inference.generate_audio(model_id, "Hallo, dies ist ein Test der Sprachsynthese.", ref, txt, out)
                elapsed = time.perf_counter() - t0
                metrics = dict((k, round(v, 4)) for k, v, _ in evaluator.evaluate(str(out), str(ref), "Hallo, dies ist ein Test der Sprachsynthese.", elapsed))
                print(f"[verify] {model_id}: generated {out.stat().st_size} bytes in {elapsed:.2f}s | metrics={metrics}", flush=True)
        return 0

    if not port_free(args.port):
        raise RuntimeError(f"Port {args.port} is already in use. Stop the existing server and retry.")

    url = f"http://127.0.0.1:{args.port}"
    print(f"\nVoice Comparison Studio running at {url}\nPress Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    uvicorn.run(studio_backend.app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
