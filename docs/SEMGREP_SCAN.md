# Semgrep scan — current corrected result

Current hardening work is on the existing `security/platform-hardening` branch and PR #2. See [HARDENING_FOLLOWUP.md](HARDENING_FOLLOWUP.md) for complete behavior and validation boundaries.

Semgrep **1.183.0** loaded 417 rules and ran **299 applicable rules across 85 files** in the latest recorded local scan. Result: **zero findings, zero parsing errors, exit status 0**. No `nosemgrep` markers, rule removals, severity changes or application-source exclusions were used to obtain this result.

Policies: `.semgrep.yml`, `p/security-audit`, `p/python`, `p/javascript`, `p/typescript`. All test sources are included. `.semgrepignore` excludes only generated/vendor/model/binary/runtime artifacts. Semgrep metrics were disabled; registry network access was explicitly authorized by the user.

## Original findings and corrections

| Earlier finding | Correction |
|---|---|
| Two potentially tainted age subprocess calls | Recipient, identity and archive data are staged through Python file I/O; age receives generated temporary paths through fixed no-shell argv. Backups publish atomically without replacing an existing archive. |
| Python subprocess in daily-backup orchestration | Calls the backup implementation directly. The administrator-controlled off-host command remains explicit, shell-free and time-bounded. |
| Dynamic urllib readiness URL | Replaced with HTTPConnection to the literal loopback address and a validated integer port. |
| HTTP fetch for responsive fixture readiness | Replaced with a TCP readiness check against the literal local fixture address. Local browser fixture HTTP remains an intentional development-only transport. |
| JSX parsing warnings | Literal ampersands use equivalent JSX entities; production build passes. |

## Evidence and reproduction

[Raw Semgrep JSON](validation/semgrep.json) records engine version, paths and empty results/errors arrays. Registry policies can evolve; this is evidence for the recorded scan, not a frozen registry-policy snapshot.

```bash
semgrep scan --config .semgrep.yml --config p/security-audit \
  --config p/python --config p/javascript --config p/typescript \
  --metrics=off --no-git-ignore --error --json --output semgrep.json .
```

The CI Semgrep job now runs the same expanded policy and fails on findings. The earlier scan with five reviewed findings is historical and has been superseded by these code corrections. A clean static scan does not certify real model dependencies, downloaded checkpoints, public TLS, external backup operation or absence of all vulnerabilities.
