"""Non-destructive diagnostics for the unified single-environment Voice Comparison Studio."""

import json
import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)
from direct_inference import model_availability


def reference_diagnostics(path: Path) -> dict:
    """Measure reference defects without modifying audio or guessing its SNR."""
    import numpy as np
    import soundfile as sf
    try:
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
        if not audio.size or not np.isfinite(audio).all():
            return {"error": "Empty or non-finite reference audio"}
        mono = audio.mean(axis=1)
        rms = float(np.sqrt(np.mean(mono ** 2)))
        peak = float(np.max(np.abs(audio)))
        clipping = float(np.mean(np.abs(audio) >= .999))
        warnings = []
        if clipping > .001:
            warnings.append("Reference contains near-full-scale samples; inspect for clipping")
        if rms < .005:
            warnings.append("Reference is very quiet; use a clean, clearly audible recording")
        if len(mono) / rate < 3:
            warnings.append("Reference is short; a clean, longer single-speaker clip may improve identity")
        if abs(float(mono.mean())) > .02:
            warnings.append("Reference has a substantial DC offset")
        return {"duration_s": len(mono) / rate, "sample_rate": rate,
                "channels": audio.shape[1], "peak": peak, "rms_dbfs":
                20 * float(np.log10(max(rms, 1e-12))),
                "near_full_scale_fraction": clipping, "warnings": warnings,
                "transcript_check": "Manually verify the transcript matches every spoken word"}
    except Exception as exc:
        return {"error": str(exc)}


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
                "reference_diagnostics": reference_diagnostics(reference / "reference.wav"),
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
