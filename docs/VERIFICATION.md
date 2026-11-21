# Local verification report

Date: 2026-11-21. Baseline: a21f846bd3e4ecebd1f6555ab131e8902de0eafa. All development was on issues-and-fixes; main was recorded as 3fc34cba90fa446f59e1a47e7bbf1da238bc2d41.

| Check | Result |
| --- | --- |
| Baseline Python tests | 68 passed |
| Final Python tests, API/model/pacing/performance/security/storage | 84 passed; two dependency deprecation warnings |
| Non-test backend coverage | 1247 / 1871 statements, 66.65%; below 85% target |
| Security module coverage | sessions 100%; rate limiting 100%; streamed-body limit 92% |
| Existing protected contracts | 68 validated; original baseline unchanged |
| Repository boundaries | Passed |
| Python compileall | Passed |
| Frontend TypeScript and Vite production build | Passed |
| npm dependency audit | Zero vulnerabilities reported |
| Resolved Python API/test dependency audit | No known vulnerabilities found |
| Bandit complete backend scan | Zero high, three medium, four low; details in AUDIT_CURRENT.md |
| Browser responsive tests | Blocked: Playwright browser download failed; system Chromium startup denied by environment socket restrictions. No passing browser claim |
| Actual TTS/evaluator inference | Not run: approved environment, complete weights, references and model runner unavailable |
| Full installed inference environment audit/SBOM | Gate implemented; not run locally |
| Full history secrets scan | Existing GitHub CI gate retained; no local gitleaks execution |
| Hosted GitHub CI | Baseline status lookup returned no status entries; final run must be checked after publication |
| Staging, TLS/proxy validation, deployed rollback, human listening | Not executed; infrastructure/evaluators unavailable |

Commands: `python -m pytest backend/tests -q --cov=backend --cov-report=json --junitxml=reports/tests.xml`, `python scripts/check_contracts.py`, `python scripts/check_structure.py`, `python -m compileall -q backend scripts`, `npm --prefix frontend run build`, `npm audit --json` from frontend, `python -m pip_audit -r backend/requirements-test.txt`, and `python -m bandit -r backend -x backend/tests`.

Coverage was calculated from production backend files excluding backend/tests; the higher overall percentage that includes test code is not used as the acceptance result. Real-model adapters naturally retain substantial unexecuted paths. Mocked tests are not real-model proof.

Files changed are visible in the descriptive commits and GitHub diff. Backend changes cover configuration, schemas/security, persistence, audio/backup, routes/orchestration and regressions; frontend changes cover bootstrap/App/types/features/polling/error boundary; release changes cover CI integration/SBOM/model gates and practical documentation. No changes were made to main and no deployment was performed by this work.
