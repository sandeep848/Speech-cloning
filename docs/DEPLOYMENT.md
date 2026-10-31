Current upgrade requirements and process-isolation settings: [HARDENING_FOLLOWUP.md](HARDENING_FOLLOWUP.md).

# Deployment

Status: development/internal testing only. This change does not deploy production or activate timers.

## Prepare a local environment

Use the intended Python interpreter explicitly. Install the real model environment from a reviewed, compatible package set; `backend/requirements.txt` remains an unvalidated heavyweight dependency specification. API-only regression testing uses the separate pinned `backend/requirements-test.lock`.

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cd frontend
npm ci
npm run build
cd ..
```

Create `backend/.env` from the example and configure a fresh random API_TOKEN, or use accounts mode. No production secrets are generated or changed by this branch. For an isolated local development session only, explicitly set DEV_ALLOW_ANONYMOUS=1. Do not use this setting with public tunnels.

```bash
python start.py --no-browser --skip-preload
```

Missing dependencies now produce an actionable error. `--setup` explicitly installs missing packages in development; it is disabled in production. `--build` explicitly permits production frontend building. `--check` validates configuration and package imports without downloading models; it does not certify storage, checkpoints, hardware or live HTTPS. `--verify-only` runs the existing genuine synthesis/evaluation check and requires actual model dependencies and weights.

## Accounts

Initialize the database once in a private local environment, stop the service, then run:

```bash
cd backend
python manage_accounts.py create alice
python manage_accounts.py create operator --admin
```

Set AUTH_MODE=accounts and AUTH_EPOCH to a random session-rotation version, then start the API. Passwords are entered interactively and never passed in argv. There is no public registration. `reset-token USER` issues a 15-minute one-use token. POST it with a new password to `/api/password-reset`. `revoke USER` invalidates sessions, `disable USER` disables login. An explicit `claim-legacy USER` assigns previously unowned records to one verified owner; do not use it to distribute records among users whose ownership is unknown. Already-installed server references remain shared templates.

## Production preparation

The systemd unit binds to loopback, restricts writes to configured data/model directories, uses one worker and a restrictive umask. Adjust User, absolute paths and model cache paths. With ProtectHome=true, caches cannot live under a home directory. Set VOICE_STUDIO_MODEL_CACHE, HF_HOME, TTS_HOME, COSY_MODEL_DIR and QWEN_MODEL_DIR to prepared writable model locations; prepare evaluator caches as well. Do not start multiple workers against the same data directory. Provision reviewed model sources and weights before production: automatic Qwen/Cosy source/weight acquisition and DNSMOS download are refused in production. Other upstream loader behavior still needs review.

Replace the Caddy domain and validate the configuration using the installed Caddy version. Keep port 8000 inaccessible from outside the host. Caddy supplies HTTPS redirection and certificates when DNS/firewall configuration permits. Verify HTTPS, redirects, secure cookies, trusted forwarding, header policy and actual protected requests in staging. No such deployment validation was run here.

## Frontend/backend configuration identity

Configuration precedence is defaults < backend/.env < inherited process environment. A service manager injects its EnvironmentFile values into the process environment. An exported shell variable therefore overrides the dotenv file. Existing processes keep their old environment until restarted. Editing source without restart does not update a non-reloading server.

VITE_API_URL is embedded at frontend build time. Changing it requires rebuilding and redeploying Pages. The endpoint URL is public configuration, never a secret. Inspect the browser Network tab request URL, then query `/api/deployment` at that exact host. `/api/health` proves liveness only; `/api/readiness` additionally requires authentication and a database query. Neither proves real-model readiness. Set GIT_REVISION to the deployed SHA when using uvicorn directly; the launcher obtains it automatically.

A new Cloudflare quick tunnel changes the public hostname and requires a new frontend build/API URL and appropriate CORS settings. Old tunnel/backend processes can remain usable until they are stopped or their credentials are revoked. Cross-site cookies may be blocked independently of CORS. Stable same-origin hosting is operationally simpler.

## Rollback

Stop the candidate process, retain its data and logs, and restore a previously verified encrypted snapshot into a new directory. Restore the previous reviewed code/environment and point it to the restored data only in an isolated environment. Do not roll back to the original fail-open authentication for public traffic. Do not drop additive tables automatically. Resume public traffic only after security regression checks pass.
