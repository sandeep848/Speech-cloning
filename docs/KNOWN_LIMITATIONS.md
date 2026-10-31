# Remaining validation and release conditions

Current implementation evidence is in [HARDENING_FOLLOWUP.md](HARDENING_FOLLOWUP.md). Older reports describe earlier states; several of their code blockers have now been fixed.

- Real Qwen, CosyVoice, XTTS and evaluator weights have not run here. No GPU/Windows compatibility, naturalness, quality score or latency improvement is claimed. The complete real-model dependency/checkpoint environment still needs validated revision/hash locking.
- Production HTTPS, firewall/proxy trust, service account/ACL policy, actual cross-site hosting and resource consumption need staging verification. Unit/process/browser-fixture results are not a public deployment certificate.
- Off-host backup copy, daily timer operation, alert delivery, retention under failure and measured recovery objectives need the actual destination and host configuration. Local encrypted recovery and hostile-archive tests pass.
- Existing production databases were unavailable. New stores enforce relational constraints; legacy production stores must pass the explicit backed-up, exclusive-lock migration. Unknown ownership remains quarantined and is never inferred automatically.
- Production requires one API service/process per data directory. Persistent SQLite rate limits and a store lock enforce this topology; it is not a distributed deployment or an edge bot-protection service.
- Workers have actual process deadlines/cancellation and restart recovery. Synthetic process tests pass; genuine model GPU allocations, native hangs and Windows behavior still need host-specific tests. Model processes run as the service account and are not a sandbox for malicious checkpoints.
- Count and byte quotas bound storage. A reviewed user-data retention/deletion policy and maintenance procedure are still required before long-lived public service; no private recordings/history were deleted here.
- Registration is disabled; account provisioning and expiring one-use reset tokens are managed offline. No external identity provider or email reset service is configured. The password policy is documented and does not claim exhaustive breached-password screening.
- The listening UI supports private ratings and agreement calculations. A validated randomized/blinded multi-rater study, expanded German benchmark corpus and empirical research outputs still require separate implementation and participants.
- Public Git history already contains reference audio/transcripts. API authorization cannot hide GitHub-hosted recordings. No history rewrite or recording deletion is authorized/performed in this work.
- Dependency Review requires enabling the repository Dependency graph. The connector has no operation to change that administrative setting. Its failing check remains intact.
- Branch protections and production approvals must be configured in GitHub. The candidate remains a draft for review; no merge or public deployment has been performed.

Expanded Semgrep currently reports no findings or parsing errors, without suppression. Bandit has no high/medium findings; low-severity audit results are retained. These results do not prove that all vulnerabilities are absent.
