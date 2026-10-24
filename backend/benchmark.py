"""Repeatable sequential GPU benchmark; saves real WAVs, timings and metric errors."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env', override=False)
from models import MODEL_IDS, _request_language, generate_audio, get_engine
from evaluator import Evaluator

SCRIPTS = {
    'German': [
        'Hallo, ich bin wieder da. Heute testen wir die Stimme, mit kurzen Pausen, und klaren Sätzen.',
        'Dr. Müller kommt um zehn Uhr. Der Preis beträgt 1,85 Euro, und morgen geht es weiter.',
        'Eine natürliche Stimme bleibt auch bei längeren Texten verständlich. Sie beendet jeden Satz vollständig, ohne zusätzliche Wörter zu erfinden.',
    ],
    'English': [
        'Hello, I am back. Today we test the voice, with short pauses, and clear sentences.',
        'Dr. Miller arrives at ten. The price is 1.85 euros, and tomorrow we continue.',
        'A natural voice stays clear even during longer passages. It finishes every sentence completely, without inventing extra words.',
    ],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=SCRIPTS, default='German')
    parser.add_argument('--reference', type=Path, default=Path(__file__).parent / 'reference/reference.wav')
    parser.add_argument('--transcript', type=Path, default=Path(__file__).parent / 'reference/reference.txt')
    parser.add_argument('--models', nargs='+', choices=MODEL_IDS, default=list(MODEL_IDS))
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'data/benchmarks')
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions must be positive')
    transcript = args.transcript.read_text(encoding='utf-8').strip()
    if not args.reference.is_file() or not transcript:
        parser.error('Provide a reference WAV and its exact nonempty transcript')
    run_dir = args.output / time.strftime('%Y%m%d-%H%M%S')
    run_dir.mkdir(parents=True, exist_ok=False)
    import torch
    report = {'python': platform.python_version(), 'torch': torch.__version__,
              'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
              'language': args.language,
              'reference_sha256': hashlib.sha256(args.reference.read_bytes()).hexdigest(),
              'transcript_sha256': hashlib.sha256(transcript.encode()).hexdigest(),
              'settings': {k: v for k, v in os.environ.items()
                           if k.startswith(('QWEN_', 'XTTS_', 'COSY_'))
                           or k in ('MODEL_DEVICE', 'MAX_INTERNAL_PAUSE_MS')},
              'records': [], 'summary': {}}
    token = _request_language.set(args.language)
    try:
        for model in args.models:
            try:
                get_engine(model)
                # Warmup excluded from trials. Sequential trials avoid GPU contention.
                generate_audio(model, SCRIPTS[args.language][0], args.reference,
                               transcript, run_dir / f'{model}-warmup.wav')
                for script_id, text in enumerate(SCRIPTS[args.language]):
                    for repetition in range(args.repetitions):
                        output = run_dir / f'{model}-{script_id}-{repetition}.wav'
                        started = time.perf_counter()
                        generate_audio(model, text, args.reference, transcript, output)
                        report['records'].append({'model': model, 'script': text,
                            'audio': str(output), 'elapsed': time.perf_counter() - started})
            except Exception as exc:
                report['records'].append({'model': model, 'error': str(exc)})
            # Save partial progress even if the next engine fails.
            (run_dir / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        # Load evaluation models after all timed synthesis to reduce contention.
        evaluator = Evaluator()
        for row in report['records']:
            if 'audio' not in row:
                continue
            row['metrics'] = {k: v for k, v, _ in evaluator.evaluate(
                row['audio'], str(args.reference), row['script'], row['elapsed'])}
            row['metric_errors'] = dict(evaluator.errors)
        for model in args.models:
            rows = [r for r in report['records'] if r['model'] == model and 'metrics' in r]
            keys = {k for r in rows for k in r['metrics']}
            report['summary'][model] = {k: statistics.median(
                r['metrics'][k] for r in rows if k in r['metrics']) for k in keys}
    finally:
        _request_language.reset(token)
        (run_dir / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(run_dir / 'results.json')


if __name__ == '__main__':
    main()
