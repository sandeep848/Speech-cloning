"""Validate deployment configuration before accepting requests."""
import os


def local_development():
    return os.getenv('STUDIO_ENV') == 'development' and os.getenv('ALLOW_INSECURE_LOCAL') == '1'


def validate_environment(origins):
    token = os.getenv('API_TOKEN', '')
    if not token and not local_development():
        raise RuntimeError('API_TOKEN is required; local-only development needs STUDIO_ENV=development and ALLOW_INSECURE_LOCAL=1')
    if os.getenv('STUDIO_ENV') in ('production', 'staging'):
        if len(token) < 32:
            raise RuntimeError('API_TOKEN must contain at least 32 characters')
        if os.getenv('COOKIE_SECURE') != '1':
            raise RuntimeError('COOKIE_SECURE=1 and HTTPS are required')
        if os.getenv('ALLOW_INSECURE_LOCAL') == '1':
            raise RuntimeError('Insecure local access is forbidden in staging/production')
        if not origins or any(not value.startswith('https://') or '*' in value for value in origins):
            raise RuntimeError('CORS_ORIGINS must contain explicit HTTPS origins')
    ttl = int(os.getenv('SESSION_TTL_SECONDS', '43200'))
    if not 60 <= ttl <= 604800:
        raise RuntimeError('SESSION_TTL_SECONDS must be between 60 and 604800')
    if int(os.getenv('WEB_CONCURRENCY', '1')) != 1:
        raise RuntimeError('Use exactly one Uvicorn application process')
