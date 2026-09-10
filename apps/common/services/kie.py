"""HTTP client for the KIE.ai API.

KIE exposes several request shapes behind one host:

* ``/api/v1/jobs/createTask`` - the unified generation endpoint used by most
  image, video and audio models, polled through ``/api/v1/jobs/recordInfo``.
* dedicated endpoints for the older product APIs (Suno, Veo, Runway, 4o Image,
  Flux Kontext...), each with its own polling route, which the catalog reads
  from the documentation rather than hard-coding here.
* per-model chat endpoints speaking the OpenAI, Anthropic or Gemini protocol.

This module is only concerned with talking to those endpoints; deciding what to
send is the callers' job (see ``apps.generation.services`` and
``apps.chat.services``).
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class KieError(Exception):
    """A call to KIE.ai failed."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload


class MissingApiKey(KieError):
    """The user has not configured a KIE.ai API key yet."""

    def __init__(self) -> None:
        super().__init__("Add your API key in Settings before generating anything.")


class KieClient:
    """A thin, synchronous wrapper around the KIE.ai HTTP API."""

    def __init__(self, api_key: str, *, timeout: int | None = None):
        if not api_key:
            raise MissingApiKey()
        self.api_key = api_key
        self.timeout = timeout or settings.KIE_REQUEST_TIMEOUT
        self.base_url = settings.KIE_API_BASE.rstrip("/")
        self.upload_url = settings.KIE_UPLOAD_BASE.rstrip("/")

    # -- plumbing --------------------------------------------------------- #

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        base: str | None = None,
        json_body: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
    ) -> Any:
        url = f"{base or self.base_url}{path}"
        request_headers = dict(self._headers)
        if files is not None:
            request_headers.pop("Content-Type", None)
        if headers:
            request_headers.update(headers)

        try:
            response = requests.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=request_headers,
                files=files,
                data=data,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise KieError("The provider did not respond in time. Try again in a moment.") from exc
        except requests.RequestException as exc:
            raise KieError(f"Could not reach the provider: {exc}") from exc

        return self._unwrap(response, url)

    def _unwrap(self, response: requests.Response, url: str) -> Any:
        try:
            payload = response.json()
        except ValueError:
            if response.ok:
                return response.text
            raise KieError(
                f"The provider returned {response.status_code} for {url}",
                status=response.status_code,
                payload=response.text[:500],
            )

        # KIE wraps most responses as {"code": 200, "msg": ..., "data": ...},
        # but the chat protocols answer with the upstream provider's shape.
        if isinstance(payload, dict) and "code" in payload and "data" in payload:
            code = payload.get("code")
            if code != 200:
                raise KieError(
                    payload.get("msg") or f"The provider rejected the request (code {code}).",
                    status=code,
                    payload=payload,
                )
            return payload.get("data")

        if not response.ok:
            message = ""
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = error.get("message", "")
                message = message or payload.get("msg") or payload.get("message") or ""
            raise KieError(
                message or f"The provider returned {response.status_code}.",
                status=response.status_code,
                payload=payload,
            )
        return payload

    # -- account ---------------------------------------------------------- #

    def credits(self) -> int | None:
        """Remaining account credits, or ``None`` if the call fails."""
        try:
            data = self._request("GET", "/api/v1/chat/credit")
        except KieError:
            logger.info("Could not read the KIE credit balance", exc_info=True)
            return None
        if isinstance(data, (int, float)):
            return int(data)
        if isinstance(data, dict):
            for key in ("credits", "credit", "balance"):
                if isinstance(data.get(key), (int, float)):
                    return int(data[key])
        return None

    def verify_key(self) -> bool:
        """Whether the configured key is accepted by KIE.ai."""
        try:
            self._request("GET", "/api/v1/chat/credit")
        except KieError as exc:
            if exc.status in {401, 403}:
                return False
            raise
        return True

    # -- uploads ---------------------------------------------------------- #

    def upload_file(self, name: str, content: bytes, *, upload_path: str = "images") -> str:
        """Upload a local file and return the URL KIE will read it from.

        KIE's generation endpoints only accept URLs, so reference images have to
        be pushed to their file service first.  Uploads there expire after 24
        hours, which is why we also keep our own copy of every input.
        """
        data = self._request(
            "POST",
            "/api/file-stream-upload",
            base=self.upload_url,
            files={"file": (name, content)},
            data={"uploadPath": upload_path, "fileName": name},
        )
        url = ""
        if isinstance(data, dict):
            url = data.get("downloadUrl") or data.get("fileUrl") or data.get("url") or ""
        if not url:
            raise KieError("The upload succeeded but no file URL was returned.", payload=data)
        return url

    # -- generation ------------------------------------------------------- #

    def create_job(self, model_id: str, model_input: dict, *, callback_url: str = "") -> str:
        """Start a task on the unified jobs endpoint and return its task id."""
        body: dict[str, Any] = {"model": model_id, "input": model_input}
        if callback_url:
            body["callBackUrl"] = callback_url
        data = self._request("POST", "/api/v1/jobs/createTask", json_body=body)
        return self._task_id(data)

    def create_task(self, path: str, body: dict, *, callback_url: str = "") -> str:
        """Start a task on one of the dedicated product endpoints.

        These are the APIs that predate ``/jobs/createTask`` - Suno, Veo,
        Runway and friends - which take their parameters at the top level
        rather than nested under ``input``.
        """
        payload = dict(body)
        if callback_url:
            payload.setdefault("callBackUrl", callback_url)
        data = self._request("POST", path, json_body=payload)
        return self._task_id(data)

    @staticmethod
    def _task_id(data: Any) -> str:
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("taskId", "task_id", "id", "recordId"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        raise KieError("The request was accepted but no task id was returned.", payload=data)

    def job_status(self, task_id: str) -> dict:
        data = self._request("GET", "/api/v1/jobs/recordInfo", params={"taskId": task_id})
        return data if isinstance(data, dict) else {"data": data}

    def task_status(self, poll_path: str, task_id: str) -> dict:
        """Poll a dedicated product endpoint, using the route its docs declare."""
        if not poll_path:
            raise KieError("No status endpoint is known for this model.")
        data = self._request("GET", poll_path, params={"taskId": task_id})
        return data if isinstance(data, dict) else {"data": data}

    # -- chat ------------------------------------------------------------- #

    def chat(self, path: str, body: dict) -> dict:
        """Call a chat endpoint and return the provider's raw response."""
        data = self._request("POST", path, json_body=body)
        return data if isinstance(data, dict) else {"raw": data}


def client_for(user) -> KieClient:
    """Build a client from a user's stored API key."""
    user_settings = getattr(user, "settings", None)
    if user_settings is None:
        raise MissingApiKey()
    return KieClient(user_settings.kie_api_key)
