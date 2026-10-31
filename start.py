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
SETUP_ALLOWED = False
BUILD_ALLOWED = False

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


def ensure_dependencies_installed() -> None:
    """On first-time deployment on a new machine, install any missing Python packages."""
    missing = [
        pkg
        for mod, pkg in REQUIRED_IMPORTS.items()
        if importlib.util.find_spec(mod) is None
    ]
    if missing and not SETUP_ALLOWED:
        raise RuntimeError("Missing dependencies: " + ", ".join(missing) + ". Install the reviewed environment first, or use --setup in development.")
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
    if os.getenv("APP_ENV") == "production" and not BUILD_ALLOWED:
        raise RuntimeError("Production frontend assets missing or stale. Build once with --build before startup.")
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
    os.environ.setdefault("GENERATION_LANGUAGE", "German")
    os.environ.setdefault("ENABLE_WHISPER", "1")
    os.environ.setdefault("ENABLE_ECAPA", "1")
    os.environ.setdefault("ENABLE_SCOREQ", "1")
    os.environ["USE_TF"] = "0"
    os.environ["USE_FLAX"] = "0"
    os.environ["PYTHONUNBUFFERED"] = "1"


    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--verify-only", action="store_true", help="Preload all models/metrics and run a full synthesis + evaluation check, then exit.")
    parser.add_argument("--setup", action="store_true", help="Explicitly install missing packages (development only)")
    parser.add_argument("--build", action="store_true", help="Explicitly build frontend assets")
    parser.add_argument("--skip-preload", action="store_true", help="Load engines lazily on first generation")
    parser.add_argument("--check", action="store_true", help="Validate configuration and imports without starting models")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    global SETUP_ALLOWED, BUILD_ALLOWED
    SETUP_ALLOWED, BUILD_ALLOWED = args.setup, args.build
    sys.path.insert(0, str(BACKEND))
    import security
    security.validate_configuration()
    if security.production() and args.setup:
        parser.error("--setup is disabled in production; prepare the reviewed environment before deployment")
    if args.check:
        missing = [pkg for mod,pkg in REQUIRED_IMPORTS.items() if importlib.util.find_spec(mod) is None]
        if missing:
            raise RuntimeError("Missing model/runtime packages: " + ", ".join(missing))
        print("Configuration and imports verified; real model execution not tested")
        return 0
    if not args.verify_only and not port_free(args.port):
        raise RuntimeError(f"Port {args.port} is occupied. Check the existing process; no process was terminated.")
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        os.environ.setdefault("GIT_REVISION", revision)
    except (OSError, subprocess.SubprocessError):
        os.environ.setdefault("GIT_REVISION", "unknown")
    ensure_dependencies_installed()
    build_frontend()

    if not args.verify_only and not port_free(args.port):
        raise RuntimeError(f"Port {args.port} is already in use. Stop the existing server and retry.")

    sys.path.insert(0, str(BACKEND))
    import direct_inference
    import backend as studio_backend

    print(f"Using unified Python environment: {sys.executable}", flush=True)
    print("Preparing and loading all TTS models and metric evaluators...", flush=True)
    if not args.skip_preload:
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
