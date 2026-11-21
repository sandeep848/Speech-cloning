"""Actual-model release gate. Run only on a provisioned trusted inference host."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from models import MODEL_IDS, generate_audio
from evaluator import Evaluator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=Path('model-smoke.json'))
    args = parser.parse_args()
    reference = Path(os.environ['SMOKE_REFERENCE_AUDIO'])
    transcript = Path(os.environ['SMOKE_REFERENCE_TEXT']).read_text().strip()
    if not reference.is_file() or not transcript:
        raise SystemExit('A consented reference recording and transcript are required')
    import torch
    seed = 42
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    report = {
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'python': platform.python_version(), 'platform': platform.platform(), 'seed': seed,
        'timestamp': time.time(), 'device': os.getenv('MODEL_DEVICE', 'auto'),
        'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        'dependencies': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
        'records': [], 'passed': False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix='studio-smoke-') as directory:
            for model in MODEL_IDS:
                for repetition in range(2):
                    if torch.cuda.is_available():
                        torch.cuda.reset_peak_memory_stats()
                    start = time.perf_counter()
                    output = Path(directory) / f'{model}-{repetition}.wav'
                    generate_audio(model, 'Hallo Welt. Dies ist ein Funktionstest.', reference, transcript, output)
                    samples, rate = sf.read(output)
                    if rate <= 0 or samples.size == 0 or not np.isfinite(samples).all() or not np.any(samples):
                        raise RuntimeError(f'{model} returned invalid or silent audio')
                    report['records'].append({
                        'model': model, 'repetition': repetition, 'sample_rate': rate,
                        'duration_s': len(samples) / rate, 'elapsed_s': time.perf_counter() - start,
                        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                        'process_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        'cuda_peak_allocated_bytes': torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None,
                        '_output': str(output),
                    })
            evaluator = Evaluator()
            required = {'wer', 'cer', 'ecapa_similarity', 'scoreq', 'dnsmos_sig', 'dnsmos_bak', 'dnsmos_ovrl', 'rtf'}
            for record in report['records']:
                values = evaluator.evaluate(record['_output'], str(reference),
                                            'Hallo Welt. Dies ist ein Funktionstest.', record['elapsed_s'])
                record['metrics'] = {name: float(value) for name, value, _ in values}
                record['metric_errors'] = dict(evaluator.errors)
                if not required.issubset(record['metrics']) or evaluator.errors:
                    raise RuntimeError('Required real-model evaluation metrics unavailable')
                if not all(np.isfinite(value) for value in record['metrics'].values()):
                    raise RuntimeError('Nonfinite evaluation value')
            report['passed'] = True
    finally:
        for record in report['records']:
            record.pop('_output', None)
        args.report.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(args.report)


if __name__ == '__main__':
    main()
