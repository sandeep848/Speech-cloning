# Testing and measured evidence

Run lightweight verification from the repository root, using your selected Python environment:

```bash
python -m pip install -r backend/requirements-test.txt pytest-cov
python -m pytest backend/tests -q --cov=backend --cov-report=html --junitxml=reports/tests.xml
python scripts/check_contracts.py
python scripts/check_structure.py
python -m compileall -q backend scripts
npm --prefix frontend ci
npm --prefix frontend run build
cd frontend
npx playwright install --with-deps chromium
npm run test:responsive
npm audit --audit-level=moderate
```

The Python tests use temporary synthetic recordings and mocked engines. They establish API/orchestration behavior, not real model compatibility, voice quality or GPU performance. CI's integration workflow uses FastAPI TestClient, SQLite, real file handling and mock synthesis; it is not a complete browser-to-real-model test.

Contract checks retain all 68 existing protected definitions, routes and request classes. Extracted request schemas are resolved through their compatibility imports, so moving their source does not remove field validation from the check. Intentional breaking changes require a reviewed migration; do not regenerate the baseline to conceal failures.

## Real hardware gate

Use an approved host with all three models, weights, evaluator dependencies and a consented reference recording. Keep one Python environment; exact full dependency compatibility remains to be established on that host. Provision pip-audit as release tooling in that environment.

```bash
export SMOKE_REFERENCE_AUDIO=/absolute/path/reference.wav
export SMOKE_REFERENCE_TEXT=/absolute/path/reference.txt
export MODEL_DEVICE=cuda:0
export ENABLE_WHISPER=1 ENABLE_ECAPA=1 ENABLE_SCOREQ=1 ENABLE_DNSMOS=1
python scripts/model_smoke.py --report model-smoke.json
python -m pip_audit --format cyclonedx-json --output runtime-sbom.json
python backend/benchmark.py --language German --repetitions 3
python backend/benchmark.py --language English --reference /absolute/path/reference_en.wav --transcript /absolute/path/reference_en.txt --repetitions 3
```

The smoke gate repeats actual generation twice per model, rejects invalid/silent output, records checksums, package versions, device, seed, latency, process peak RSS and CUDA peak allocations, and requires the existing WER/CER/ECAPA/SCOREQ/DNSMOS/RTF metrics. Process peak RSS is cumulative, not isolated per-model memory. Seeding does not guarantee bitwise determinism across devices, libraries or concurrent runs. Model checkpoint revisions are not yet pinned by the gate.

Benchmark scripts cover German/English sentences and punctuation. Approved quality baselines, genuine long-form scripts, regression thresholds supported by evidence, multiple reference voices, blind listener studies and human ratings still require actual execution. No scores, listening results or model benchmarks were fabricated.

## Latest local verification

See VERIFICATION.md for exact measured results and execution limits. The 85% non-test backend and 90% security coverage targets are not equivalent to overall coverage including test files. Report them separately.
