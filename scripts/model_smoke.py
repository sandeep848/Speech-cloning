"""Real model release check. Run only on a trusted, provisioned inference host."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from models import MODEL_IDS, generate_audio

reference = Path(os.environ['SMOKE_REFERENCE_AUDIO'])
transcript = Path(os.environ['SMOKE_REFERENCE_TEXT']).read_text().strip()
if not reference.is_file() or not transcript:
    raise SystemExit('A consented reference recording and transcript are required')
results = []
with tempfile.TemporaryDirectory(prefix='studio-smoke-') as directory:
    for model in MODEL_IDS:
        start = time.perf_counter()
        output = Path(directory) / f'{model}.wav'
        generate_audio(model, 'Hallo Welt. Dies ist ein Funktionstest.', reference, transcript, output)
        samples, rate = sf.read(output)
        if rate <= 0 or samples.size == 0 or not np.isfinite(samples).all() or not np.any(samples):
            raise RuntimeError(f'{model} returned invalid or silent audio')
        results.append({'model': model, 'sample_rate': rate,
                        'duration_s': len(samples) / rate,
                        'elapsed_s': time.perf_counter() - start})
print(json.dumps(results, indent=2))
