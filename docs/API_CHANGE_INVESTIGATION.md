# API configuration and continued access investigation

The original `authenticated()` returned True whenever API_TOKEN was missing. That is a confirmed fail-open defect, reproduced by the baseline source and replaced with explicit anonymous-development opt-in and production validation. The original cookie was an HMAC of a timestamp signed by API_TOKEN, accepted for 12 hours. Existing sessions were not separate users and logout did not revoke a stolen cookie. New sessions are opaque, stored by digest, expire after one hour and can be revoked server-side.

The exact live incident cannot be attributed to one cause without observing its processes, environment and browser Network requests. The following distinguishes tested behavior from operational explanations.

| Change | What happens / required action | Evidence |
|---|---|---|
| API_TOKEN | Running processes retain their environment. After completed restart with a new token, old bearer tokens and sessions fail; new login succeeds. | Real local uvicorn restart/HTTP test; unit rotation tests. |
| ADMIN_TOKEN | Independent reference-reload credential; user token is insufficient. Restart when changing external configuration. Production accounts mode additionally requires an admin account session. | Separate admin token regression; deployed role/TLS flow still unverified. |
| Remove API_TOKEN | Protected routes remain locked by default; token-mode production refuses startup. Development access requires explicit opt-in. In accounts mode API_TOKEN is not the identity source. | Missing-credential tests and local HTTP restart. |
| Edit backend/.env | dotenv loads once with override=False. Restart the backend; verify an exported/service variable is not overriding the file. | Source inspection; not a live-host dotenv experiment. |
| Change exported shell variable | Changes the environment of subsequently launched children, not an already-running backend. Restart from the intended shell/service. | Process semantics/source inspection. |
| Change backend URL | A URL points to a server; it is not an access credential. An older reachable server can continue serving requests. Verify the actual request host and deployment identity. | Source/configuration inspection; live site not inspected. |
| Change VITE_API_URL | Vite embeds it at build time. Rebuild and redeploy the frontend. Changing the backend environment alone cannot change an existing Pages bundle. | Vite/frontend source inspection. |
| Restart frontend only | Backend process and its token/session configuration are unchanged. | Architecture inspection. |
| Restart backend only | Backend loads current effective configuration; existing frontend still points to its built URL. New token rejects old sessions after rotation. | Local HTTP restart test and source inspection. |
| Redeploy GitHub Pages | Publishes a newly built bundle. Check repository variable, build commit and browser request URL; refresh old assets. | Workflow inspection; Pages not redeployed. |
| Start new Cloudflare tunnel | Creates a new public hostname without necessarily stopping the old tunnel/backend. Update URL and CORS, rebuild frontend, then verify authentication at the intended host. | Operational inference; no new tunnel started. |
| Keep old backend running | Its old environment and old token remain active until stopped/reconfigured. A changed file on disk does not revoke that separate process. | Source/process reasoning; no old production process accessed. |
| Edit backend source without restart | uvicorn in the supplied production unit has no reload. Restart to run changed code. | Launcher/unit source inspection. |
| Reuse browser session | New sessions last one hour. A still-valid session can legitimately allow access without entering the token again. Rotation/expiry/revocation invalidates it. | Cookie expiry, tampering, logout replay and rotation regressions. |

## Verify a specific deployment

1. In the browser Network tab copy the exact request URL, without copying credentials.
2. Read `/api/deployment` on that host and compare the intended software revision/environment.
3. Start a fresh browser context and confirm a protected read fails without credentials; public `/api/health` alone is insufficient.
4. Log in, then perform an authorized credential-rotation drill on staging. Confirm old bearer tokens/cookies fail and the new credential works. Never rotate production credentials solely to diagnose this candidate.
5. Inspect process/service start times and the sources of effective non-secret configuration. Do not print environment files or credential values into logs or issue reports.

No live credentials, browser sessions, production data or tunnels were changed in this work.
