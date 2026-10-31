# Audit remediation

Reviewed baseline: 8be737a130f580d351d0d402c33fa4d9074c9528.

## Changes

- Reject unknown admin paths before body consumption or rate-limit allocation. Group mutation limits into a finite set of categories instead of attacker-controlled paths.
- Separate high-volume per-source login admission from failed-password limits keyed by source and account. Successful logins do not consume the failed-password bucket. Argon2 concurrency remains bounded and limits persist in SQLite.
- Authenticate private requests before body buffering; admit at most four concurrent reference uploads. Preserve duplicate response headers. Same-origin cookies now use SameSite=Lax; cross-origin frontends explicitly request an in-memory session transport.
- Clear scripts, recordings, reviewer identity, metrics and local batch identifiers on logout/session expiry/account changes. Discard responses and private downloads that cross a session boundary.
- Remove personal reference WAVs and transcripts from the branch and ignore new private reference transcripts. API profiles omit transcripts. Tests create a synthetic tone in a temporary directory.
- Fix date ordinals and multiword German abbreviations while retaining genuine boundaries after sentence-final abbreviations. The rule intentionally protects explicit ordinal/date contexts rather than every number before an uppercase word.
- Persist observed first-audio timing and generation language in job_observations. Pass both across the evaluation-worker pipe and preserve timing for cached audio. Whisper receives the selected language.
- Match Microsoft DNSMOS P.835 non-personalized repetition, hop size and polynomial calibration. Preserve separately named raw scores. Verify checkpoint SHA-256 before loading: 269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd. The bundled file was compared byte for byte by hash with Microsoft's DNS-Challenge model.
- Scope and restore PyTorch deterministic policy and RNG state, including exceptions. Separate worker lock-wait deadlines from execution deadlines. Log worker tracebacks privately. Report supervisor state from the API instead of child-local module state.
- Bound Qwen/XTTS embedding caches, CosyVoice registered speakers and timing records. Resolve Qwen's exact configured snapshot rather than picking one by modification time. Production requires an immutable QWEN_REVISION; manually provisioned directories require a matching .studio-revision marker.
- Add owned reference listing/deletion and generation deletion. DELETE /api/jobs/{id} cancels active jobs and deletes terminal jobs. Shared/cached files remain until their last database reference is deleted. Evaluation in progress and references still used by generations return 409. Database ownership is checked before every deletion. The browser offers saved-data management.
- Add opt-in daily retention with RETENTION_DAYS (0 disables). Active generation/evaluation and shared server references are preserved. Configure retention only after agreeing the research-data policy. File removal follows the database commit.
- Enable SQLite WAL; move authentication storage, uploads, generation reference resolution, transaction/quota planning and evaluation admission off the event loop. Database contention still needs target-host load testing.
- Add language to batch requests and explicit reviewer identifiers to the rating interface. Kappa compares two named observers on shared recordings within the owning studio; it does not expose another account's data.
- Enable configured evaluators in .env.example. Require explicit XTTS model-license acceptance through COQUI_TOS_AGREED=1. Verification exits unsuccessfully if evaluators are unavailable and respects REFERENCE_DIR.
- Configure concrete writable data/model/evaluator/cache paths in the systemd example. Add account listing and enabling, an Apache-2.0 license matching README, static checks and coverage gating. Frontend build tools are development dependencies.
- Remove unsupported dependency-review-action. Blocking pip-audit and npm-audit checks remain on pull requests. This provides installed-lockfile vulnerability checks, not GitHub's dependency-diff/licensing service. Remove duplicate full pytest execution; encrypted restore tests retain their dedicated age-enabled job.

## API changes

- GET /api/references: list the caller's uploaded references.
- DELETE /api/references/{id}: remove an unused owned reference; shared references remain administrator-managed.
- DELETE /api/generations/{id}: delete terminal jobs, metrics, ratings and unused audio in an owned generation.
- DELETE /api/jobs/{id}: cancel active inference, or delete a terminal job.
- POST /api/batches accepts language (German, English, de, en).
- Reference-profile responses no longer include reference transcripts.
- The admission queue remains intentionally bounded. Large experiments must be submitted in waves within MAX_PENDING_JOBS and MAX_USER_JOBS; the schema's maximum input size is not a promise of available queue capacity.

## Upgrade

1. Back up and verify restore before upgrading a service with existing data. New observation metadata is additive; the existing explicit legacy relational migration requirement remains.
2. Store consented reference.wav/reference.txt in a private directory and set REFERENCE_DIR to that path. English references use reference_en.wav/reference_en.txt. Source checkout no longer includes a default person's voice.
3. Use AUTH_MODE=accounts for separate people. Token mode intentionally identifies one shared studio principal and cannot provide isolation among token holders.
4. Production requires HTTPS, COOKIE_SECURE=1 and explicit HTTPS CORS origins. Provision the exact reviewed Qwen revision using the Hugging Face cache, or verify a local directory and write its immutable revision into .studio-revision. A marker asserts operator verification; it is not a cryptographic manifest of every weight file.
5. Review model licenses and explicitly opt in where required. Set EVAL_MODELS_DIR and SCOREQ_CACHE to provisioned writable locations. Check upstream libraries' cache configuration on the actual host.
6. Run python start.py --verify-only on the real model environment. Only after successful real synthesis/evaluation, capture its exact packages with python backend/capture_environment.py --output deploy/model-environment.lock. Review that lock and model/source revisions; install it in a fresh environment and repeat verification. No untested heavyweight lock is claimed by this change.
7. Rebuild/redeploy the frontend and restart the backend. Existing built assets and running processes do not automatically change with source code.

## Remaining operational work

- Historical reference files still exist in old commits and public copies. This ordinary branch cannot erase them. A separately coordinated history rewrite and force-push, plus treatment of existing copies as disclosed, is required. No remote history was rewritten.
- Activate and verify the existing daily backup timer and off-host destination on the deployment host. A repository change cannot establish that backups are running.
- Real model quality/latency, CUDA operations, Windows integration, upstream compatibility shims and the full heavyweight environment require target-host verification. No fabricated model scores or compatibility claims are supplied.
- Browser regression results and hosted CI status must be checked before merging. Local browser execution may be restricted by the execution environment.
- Broad file-splitting and removing compatibility parameters are deferred to a separate behavior-preserving refactor; they are not security fixes. Public liveness/deployment identity endpoints remain intentional for diagnosis.
