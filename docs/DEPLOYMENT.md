# Release and recovery

The existing application supports a shared, trusted research workspace. Do not expose it as a multi-user production service until the remaining identity, ownership and durability work in IMPLEMENTATION_STATUS.md is complete.

## Validation before release

1. Confirm the exact candidate commit passes **Final checks**. Review scan output, code-owner approval and all regression results. Review changed dependencies and model compatibility.
2. Provision a trusted isolated Python 3.12 inference host with supported GPU/runtime, consented reference audio and all three model packages and weights. Do not install or download heavyweight models on shared PR runners.
3. Add runner labels `self-hosted`, `linux`, `x64`, `voice-studio-models`. Configure the `model-validation` environment with required reviewers and restrict allowed branches. Set environment variables `SMOKE_REFERENCE_AUDIO` and `SMOKE_REFERENCE_TEXT` to local files on the runner. Run **Real model release validation** manually for the exact candidate SHA/ref. This workflow is independent of routine CI; its success is a separate release requirement.
4. Back up SQLite with its online backup API while the app is live, or stop the service before copying it. Back up the complete data/audio directory and server reference/configuration separately. Treat reference audio and tokens as private. Rehearse restoration to an isolated host.
5. Test the candidate on an isolated staging machine with copies of non-sensitive test data. Keep **one uvicorn worker**: jobs are process-local. Verify login, references, every model, evaluations, downloads, ratings, restart handling and disk limits. `STUDIO_ENV=production` requires an API token of at least 32 characters and `COOKIE_SECURE=1`; HTTPS must terminate at the trusted proxy. These checks do not create multi-user isolation.
6. Stop admissions and wait for active jobs to finish before restarting. Do not copy an active SQLite file or start two competing API instances on the same data directory. Record revision, environment versions, configuration fingerprint, model smoke evidence and backup location without storing secrets in Git.
7. After release, verify health, authorized model/reference endpoints and a real generated recording. Monitor errors and storage. For regressions stop admissions, stop the service, restore the previous code/runtime, and restore the database/audio snapshot if data/schema changes require it. Validate before reopening access.

## Frontend Pages deployment

The manual Pages workflow accepts main only and runs the coordinator before building/uploading the frontend artifact. The deployment job publishes that uploaded artifact under the `github-pages` environment. Configure environment reviewers and deployment-branch restrictions in repository settings. No backend is deployed by Pages. CORS origins and `VITE_API_URL` must match the real HTTPS backend.

This branch deliberately cannot deploy to the existing live Pages site. Testing the branch does not require merging it. Automatic backend production promotion is not configured because no staging/production target, credentials, backup destination or validated model runtime is provided.
