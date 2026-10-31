# Incident response

1. Restrict public ingress and preserve protected logs plus a consistent encrypted snapshot. Do not publish private audio, passwords, bearer tokens, reset tokens or database files in issue reports.
2. Identify the affected deployed SHA, backend hostname and configuration source. Record request IDs and affected resource IDs without copying private transcripts unnecessarily.
3. Rotate only credentials whose rotation is explicitly authorized by the operator. API_TOKEN rotation revokes shared-token sessions; AUTH_EPOCH rotation invalidates account sessions. Offline `revoke USER` or `disable USER` limits individual account access. Restart all affected processes; leaving an old backend running can preserve old access.
4. Investigate account/resource ownership, login abuse, downloads/exports, dependency findings and storage integrity. Current logs are not a complete immutable audit trail; retain host/reverse-proxy logs securely.
5. Repair and validate in isolation. Restore a verified database/audio snapshot if needed. Never overwrite the original evidence directory or route an insecure old release to the public domain.
6. Resume only after authentication, isolation, TLS, backup and relevant regression checks succeed. Document impact, recovery time, remaining risks and follow-up actions.

Legal notifications and user communications require the responsible operator's assessment and authorization. No notifications are sent automatically by this implementation.
