# Deployment quickstart

GitHub Pages serves the React frontend only. Run the Python API on an online Linux host.

- Configure a production backend with authentication enabled, secure cookies, CORS restricted to `https://sandeep848.github.io`, and an approved HTTPS reverse proxy/tunnel.
- From the activated environment, run: `python start.py --backend-only --no-browser --skip-preload --reuse`.
- `--reuse` does not create a new server: it only detects a healthy already-running server on the same port. Closing the terminal running the server still stops it unless using an approved service manager.
- In GitHub Actions repository variables, set `VITE_API_URL` to the stable **HTTPS origin** of your backend, not ending in `/api`. Select GitHub Actions as the Pages source.
- Validate `/api/health`, login, CORS and authenticated requests from the public GitHub Pages site.
- Validate real inference with `python start.py --verify-only --backend-only` only after model weights, evaluator dependencies and reference files are installed. A passing unit test does not establish that Qwen3, CosyVoice 3 or XTTS-v2 can generate.
- Multiple terminals should not start multiple servers listening on port 8000. Use `--reuse` for secondary terminals and run the original backend through an approved persistent service if desired.

Never commit `backend/.env`, passwords, generated audio containing user data or the private reference voice.
