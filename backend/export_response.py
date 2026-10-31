"""Disk-backed exports with cleanup on success, errors and disconnects."""
from fastapi.responses import FileResponse


class PrivateExportResponse(FileResponse):
    def __init__(self, path, release, **kwargs):
        self.release = release
        super().__init__(path, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()
