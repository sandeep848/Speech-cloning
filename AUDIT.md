# Audit and remediation

Audit inputs: repository main at `42b0dcd`, the uploaded fixed ZIP, and three screenshots dated 19 October 2026.

| Finding | Change |
| --- | --- |
| Missing CosyVoice and Coqui dependencies fail immediately after clicking Generate | Per-model setup diagnostics, interpreter diagnostic command, isolated worker setup, and visible skipping of unavailable cards |
| Sequential execution conflicts with simultaneous comparison | Three dispatch threads and per-model locks; a three-party barrier test verifies simultaneous adapter entry |
| Backend root returns 404 | Serve production frontend at `/` when built; otherwise return service and frontend instructions |
| Reference setup needs stable server paths and matching transcript | Preserve existing reference assets unchanged and allow a separate server reference directory |
| “internal-disabled” reference upload/edit endpoints still callable without admin authentication | Remove both routes |
| API token blocks browser playback and no login flow exists | Expiring signed HttpOnly session cookie and token login; protected audio range requests tested |
| CORS preflight intercepted by authentication | OPTIONS bypasses auth; origin checks protect state-changing browser requests |
| Unbounded submission and misleading repeated cached batch trials | Queue capacity limits; repeated trials bypass the cache and parent batch status includes generation records |
| Lost jobs stay pending after restart | Mark orphaned generation/evaluation work interrupted; preserve existing database |
| Absolute stored audio paths break when moving project | Repair missing paths from matching files inside the new data/audio directory |
| Runtime includes queue time / not exposed to users | Store measured per-job generation runtime and show seconds per model |
| Cached new jobs have no evaluation | Schedule evaluation for cached jobs too |
| Duplicate evaluation requests / swallowed failures | Deduplicate scheduling and store status and error details |
| Initialization failures vanish after first evaluation | Preserve evaluator initialization errors for subsequent recordings |
| Speaker tensors may use a different device | Place inputs on the configured SpeechBrain device |
| WER/CER affected by case and punctuation | Explicit shared text normalization |
| NaN Cohen's kappa breaks JSON | Return null with undefined status; require completed recordings for ratings |
| Frontend has stale selection, extra Experiments UI and manual metrics refresh | Validate persisted model IDs, retain two tabs, automatically poll evaluation, select the recording being rated |
| Build dependencies contain known npm advisories | Upgrade Vite/plugin, commit lockfile, use npm ci; audit passes at remediation time |
| Pages deploy runs automatically without a working API | Manual-only Pages deployment; add backend/frontend verification workflow and single-origin hosting examples |
| Unformatted backend hinders maintenance | Format Python source and tests; keep adapters and transport separate |

## Validation boundary

Automated tests cover API validation, reference import/playback, cookie/header authentication, CORS, removal of unsafe routes, simultaneous dispatch, cached recording reuse, runtime values, worker protocol handling, model diagnostics and persistent evaluation errors. Frontend TypeScript compilation and production bundling run separately.

These checks do not constitute a real model benchmark. This environment has not run the three engines with downloaded weights or GPU inference. No claim is made that all models fit your GPU, that a missing engine was installed on your machine, or that a live public deployment exists. A healthy package check means dependencies are discoverable; only synthesis with the deployed weights establishes operational readiness and quality.

Browser interaction checks were attempted but could not run because the browser executable was unavailable and its download returned a truncated archive. Frontend validation is limited to TypeScript/build and API integration checks.
