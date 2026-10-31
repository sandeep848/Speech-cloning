# Test execution

From the repository root, with a separate virtual environment:

```bash
python -m pip install -r backend/requirements-test.lock
python -m pytest backend/tests -q
python -m compileall -q backend start.py
cd frontend
npm ci
npm run build
npm audit --audit-level=moderate
npx playwright install chromium
npm run test:responsive
```

The original suite passed 60 tests at baseline `1e5b70d3b9497395ce53b94b640dfd25102f0587`. New regressions cover accounts, ownership, uploads, sessions, credential rotation, password reset, CSRF, quotas, request controls/cache/download correctness, backup integrity and real loopback HTTP restart/rotation. Synthetic model functions are used only inside isolated tests. No test claims that a production TTS model worked.

`test_live_auth.py` launches actual uvicorn processes with separate temporary data directories and proves old cookies/tokens are rejected after restart with a new token, and missing credentials keep the API closed. This is a local integration test, not a test of the university server or public staging deployment.

Fixtures use temporary SQLite databases, randomly unique synthetic account identities and deterministic generated WAV bytes. They never overwrite real recordings. Cross-user tests cover references, generation submission/history, audio, evaluations/errors, ratings and exports. Further scenarios in the master prompt remain outstanding: exhaustive role/registration behavior, every codec edge case, full load/restart/cancellation tests, all-model real weights, every browser error/accessibility path and encrypted off-host recovery.

Security tools are separate from inference dependencies. Run pip-audit against both the API lock and the final model environment, Bandit, the repository Semgrep rules, a broader reviewed Semgrep policy, Gitleaks and npm audit. The four local Semgrep rules are focused regression gates, not a full security assessment. A successful CI run is not production approval.

Encrypted recovery integration requires age and age-keygen on PATH. Without them pytest reports an explicit skip; the dedicated backup CI job installs a checksum-verified age release. The final local run included this integration test.
