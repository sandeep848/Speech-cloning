"""Non-destructive diagnostics for the unified single-environment Voice Comparison Studio."""

import json
import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)
from direct_inference import model_availability


def main():
    reference = Path(os.getenv("REFERENCE_DIR", Path(__file__).parent / "reference"))
    results = {
        model: model_availability(model) for model in ("qwen3", "cosyvoice3", "xtts-v2")
    }
    print(
        json.dumps(
            {
                "python": sys.executable,
                "reference_audio": (reference / "reference.wav").is_file(),
                "reference_text": (reference / "reference.txt").is_file(),
                "ffmpeg": shutil.which("ffmpeg"),
                "authentication_enabled": bool(os.getenv("API_TOKEN")),
                "models": results,
            },
            indent=2,
        )
    )
    return (
        0
        if all(result["configured"] for result in results.values())
        and (reference / "reference.wav").is_file()
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
