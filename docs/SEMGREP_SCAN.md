# Semgrep scan completion — 31 October 2026

The user authorized completing the previously blocked external Semgrep operation. Semgrep **1.183.0** completed the scan using the repository rules plus Semgrep Registry `p/security-audit`, `p/python`, `p/javascript` and `p/typescript`. No Semgrep Cloud account was connected. Metrics were disabled; the scan was permitted to retrieve registry policies and perform normal Semgrep network operations.

## Scope and result

- 417 rules loaded; 299 applicable rules ran across **73 files**, including all eight backend test files and the frontend responsive test.
- **No parsing errors or warnings** in the final result (`errors: []`).
- **Five findings:** four scanner ERROR results and one WARNING. These labels are rule severities, not confirmed exploit/CVSS ratings.
- Exit status **1** is expected with `--error` when findings remain. This is a completed scan with findings, **not a zero-finding pass**.
- Generated frontend dependencies/builds, Python caches, runtime data, vendor model sources/checkpoints and audio binaries are excluded explicitly in `.semgrepignore`. Tests are included. No application source files were excluded to suppress findings.
- The first scan used Semgrep's default test exclusions and found four results plus two JSX parsing warnings. Encoding the two literal JSX ampersands as `&amp;` preserved rendered text and resolved those warnings. The frontend production build passed and produced the same application JS/CSS hashes as before the text-source adjustment.

Command from the repository root:

```bash
semgrep scan --config .semgrep.yml \
  --config p/security-audit --config p/python \
  --config p/javascript --config p/typescript \
  --metrics=off --no-git-ignore --error --json \
  --output /tmp/studio-semgrep-complete-final.json .
```

Raw evidence: [validation/semgrep.json](validation/semgrep.json), SHA-256 `2eebac1fcf4f6f40085de8aa239b2a00d3811d15198024d6c54533064381bfca`. The source baseline is PR head `086839d340195042f31900d030d2feef379039dc`, plus the two semantically equivalent JSX entity edits and the explicit scan-ignore policy in this follow-up. Registry policies can evolve; the recorded JSON preserves the executed rule IDs, findings, paths and engine version. It is not a frozen copy of every registry rule.

## Findings and manual review

| Location | Scanner finding | Review and disposition |
|---|---|---|
| `backend/backup.py:102` | ERROR: potentially tainted subprocess arguments | Offline administrator CLI invokes the resolved `age` executable with an argument list and no shell. Recipient/archive inputs come from administrator arguments/configuration, not API forms. No shell-command injection path is demonstrated. Keep executable search paths and service configuration restricted. Retained as a reviewed administrative trust-boundary finding. |
| `backend/backup.py:109` | ERROR: potentially tainted subprocess arguments | Same explicit argv/no-shell design for the administrator's age decryption operation. Identity/archive paths are offline operator inputs; this helper is not an HTTP endpoint. No demonstrated remote command injection. Retained with the same configuration/filesystem assumptions. |
| `backend/daily_backup.py:22` | ERROR: potentially tainted subprocess arguments | Uses the running Python interpreter and repository-owned backup script with list arguments populated from protected service configuration. No shell or browser-controlled executable is used. The separate off-host copy command is deliberately administrator-controlled and must remain protected. Retained as a reviewed configuration trust boundary. |
| `frontend/tests/responsive.cjs:35` | ERROR: unencrypted HTTP request | Browser test waits for its own fixture server on `http://127.0.0.1:4175`; no production credentials or traffic are transmitted. Local test HTTP is the documented exception. Test-only finding; no production HTTPS policy change needed. |
| `start.py:97` | WARNING: dynamic urllib URL could permit unsafe schemes | The launcher's caller constructs `http://127.0.0.1:<validated integer port>` and appends the fixed `/api/health` path. It does not use a browser-provided URL. No arbitrary URL/file-read path is demonstrated under current callers. Keep this helper internal; arbitrary future URL callers would require explicit scheme/host validation. |

No `nosemgrep` markers, rule removals or severity changes were used. We did not apply the rule's generic `shlex.quote()` suggestion to list-based subprocess calls; shell quoting is not required by that API and would alter argument values.

No remotely exploitable vulnerability was confirmed among these five findings during source review. This statement is limited to these results and their inspected callers. It does not certify the application, its dependencies, downloaded models or a deployed environment as secure.

## Other release gates

The separate GitHub Dependency Review failure still requires enabling Dependency graph in repository settings. Real-model/GPU execution, live HTTPS/account-cookie/CSP validation, worker termination and off-host backup operation remain outside this completed static scan. The draft PR remains unsuitable for a public-production release.
