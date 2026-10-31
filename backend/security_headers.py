"""Security headers also wrap authentication failures and body-limit responses."""
import os
import uuid


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        async def protected(message):
            if message['type'] == 'http.response.start':
                original = message.get('headers', [])
                headers = {}
                headers[b'x-content-type-options'] = b'nosniff'
                headers[b'referrer-policy'] = b'same-origin'
                headers[b'x-frame-options'] = b'DENY'
                headers[b'permissions-policy'] = b'camera=(), microphone=(), geolocation=()'
                headers.setdefault(b'x-request-id', scope.get('state', {}).get('request_id', str(uuid.uuid4())).encode())
                if scope['path'].startswith('/api/'):
                    headers[b'cache-control'] = b'no-store'
                if os.getenv('APP_ENV') == 'production':
                    headers[b'strict-transport-security'] = b'max-age=31536000'
                    headers[b'content-security-policy'] = b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
                message['headers'] = [(k,v) for k,v in original if k.lower() not in headers] + list(headers.items())
            await send(message)
        await self.app(scope, receive, protected)
