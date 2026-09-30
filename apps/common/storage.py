"""Media storage that writes internally but links publicly.

The app reaches MinIO over the Docker network (``http://minio:9000``), which a
browser cannot resolve. Swapping the host in a signed URL is not enough either:
an S3 signature covers the host, so a URL signed for ``minio:9000`` is rejected
at the public domain. Instead, URLs are signed by a second client configured
with the public endpoint. Signing is local computation - that client never
makes a request - so every read and write still goes over the internal network.
"""

from __future__ import annotations

from storages.backends.s3 import S3Storage


class MediaStorage(S3Storage):
    """S3 storage whose URLs point at ``public_endpoint_url``."""

    def __init__(self, **settings):
        self._settings = dict(settings)
        self._public = None
        super().__init__(**settings)

    def get_default_settings(self) -> dict:
        return {**super().get_default_settings(), "public_endpoint_url": None}

    def _public_twin(self) -> S3Storage:
        if self._public is None:
            settings = {
                key: value
                for key, value in self._settings.items()
                if key != "public_endpoint_url"
            }
            settings["endpoint_url"] = self.public_endpoint_url
            self._public = S3Storage(**settings)
        return self._public

    def url(self, name, parameters=None, expire=None, http_method=None):
        if not self.public_endpoint_url:
            return super().url(name, parameters, expire, http_method)
        return self._public_twin().url(name, parameters, expire, http_method)
