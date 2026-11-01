"""Repeatable sequential GPU benchmark; saves real WAVs, timings and metric errors."""

from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)
from models import (  # noqa: E402
    MODEL_IDS,
    _request_language,
    generate_audio,
    get_engine,
    get_timing,
)
from doctor import reference_diagnostics  # noqa: E402
from evaluator import Evaluator  # noqa: E402

SCRIPTS = {
    "German": [
        "Hallo, ich bin wieder da. Heute testen wir die Stimme, mit kurzen Pausen, und klaren Sätzen.",
        "Dr. Müller kommt um zehn Uhr. Der Preis beträgt 1,85 Euro, und morgen geht es weiter.",
        "Eine natürliche Stimme bleibt auch bei längeren Texten verständlich. Sie beendet jeden Satz vollständig, ohne zusätzliche Wörter zu erfinden.",
    ],
    "English": [
        "Hello, I am back. Today we test the voice, with short pauses, and clear sentences.",
        "Dr. Miller arrives at ten. The price is 1.85 euros, and tomorrow we continue.",
        "A natural voice stays clear even during longer passages. It finishes every sentence completely, without inventing extra words.",
    ],
}


def metric_statistics(records: list[dict], model: str) -> dict:
    rows = [r for r in records if r["model"] == model and "metrics" in r]
    keys = {key for row in rows for key in row["metrics"]}
    result = {}
    for key in keys:
        values = sorted(
            row["metrics"][key]
            for row in rows
            if key in row["metrics"] and math.isfinite(row["metrics"][key])
        )
        if values:
            result[key] = {
                "n": len(values),
                "median": statistics.median(values),
                "mean": statistics.mean(values),
                "min": values[0],
                "max": values[-1],
                "p95_nearest_rank": values[math.ceil(0.95 * len(values)) - 1],
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=SCRIPTS, default="German")
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path(__file__).parent / "reference/reference.wav",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        default=Path(__file__).parent / "reference/reference.txt",
    )
    parser.add_argument(
        "--models", nargs="+", choices=MODEL_IDS, default=list(MODEL_IDS)
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "data/benchmarks"
    )
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    transcript = args.transcript.read_text(encoding="utf-8").strip()
    if not args.reference.is_file() or not transcript:
        parser.error("Provide a reference WAV and its exact nonempty transcript")
    run_dir = args.output / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    import torch

    report = {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "language": args.language,
        "reference_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
        "transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "settings": {
            k: v
            for k, v in os.environ.items()
            if k.startswith(("QWEN_", "XTTS_", "COSY_"))
            or k
            in (
                "MODEL_DEVICE",
                "MAX_INTERNAL_PAUSE_MS",
                "SPEECH_RATE",
                "COMMA_PAUSE_MS",
                "SENTENCE_PAUSE_MS",
                "PARAGRAPH_PAUSE_MS",
                "CONTINUATION_PAUSE_MS",
            )
        },
        "reference_diagnostics": reference_diagnostics(args.reference),
        "warmups": {},
        "records": [],
        "summary": {},
        "statistics": {},
    }
    token = _request_language.set(args.language)
    try:
        for model in args.models:
            try:
                load_started = time.perf_counter()
                get_engine(model)
                load_seconds = time.perf_counter() - load_started
                # Warmup excluded from trials. Sequential trials avoid GPU contention.
                warmup_started = time.perf_counter()
                generate_audio(
                    model,
                    SCRIPTS[args.language][0],
                    args.reference,
                    transcript,
                    run_dir / f"{model}-warmup.wav",
                )
                report["warmups"][model] = {
                    "load_s": load_seconds,
                    "first_generation_s": time.perf_counter() - warmup_started,
                }
                for script_id, text in enumerate(SCRIPTS[args.language]):
                    for repetition in range(args.repetitions):
                        output = run_dir / f"{model}-{script_id}-{repetition}.wav"
                        started = time.perf_counter()
                        generate_audio(model, text, args.reference, transcript, output)
                        report["records"].append(
                            {
                                "model": model,
                                "script": text,
                                "audio": str(output),
                                "elapsed": time.perf_counter() - started,
                                "first_audio_ms": get_timing(output, "first_audio_ms"),
                            }
                        )
            except Exception as exc:
                report["records"].append({"model": model, "error": str(exc)})
            # Save partial progress even if the next engine fails.
            (run_dir / "results.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
        # Load evaluation models after all timed synthesis to reduce contention.
        evaluator = Evaluator()
        for row in report["records"]:
            if "audio" not in row:
                continue
            row["metrics"] = {
                k: v
                for k, v, _ in evaluator.evaluate(
                    row["audio"], str(args.reference), row["script"], row["elapsed"]
                )
            }
            row["metric_errors"] = dict(evaluator.errors)
        for model in args.models:
            stats = metric_statistics(report["records"], model)
            report["statistics"][model] = stats
            report["summary"][model] = {
                key: value["median"] for key, value in stats.items()
            }

    finally:
        _request_language.reset(token)
        (run_dir / "results.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    print(run_dir / "results.json")


if __name__ == "__main__":
    main()
