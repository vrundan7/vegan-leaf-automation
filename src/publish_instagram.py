"""Official Instagram Graph API client (content publishing for professional accounts).

Flow: POST /{ig-user-id}/media (image_url, caption, alt_text) -> container id
      GET  /{container-id}?fields=status_code  until FINISHED
      POST /{ig-user-id}/media_publish (creation_id) -> media id

The token is sent in the Authorization header, never in URLs, and is redacted from every
error message.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

import requests

log = logging.getLogger(__name__)

TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613, 80001, 80002}  # unknown / temp / throttling
AUTH_CODES = {190, 102, 463, 467}                           # invalid / expired token
PERMISSION_CODES = {10, 200, 3, 100}                         # missing permission or bad parameter
_ID_RE = re.compile(r"^\d{5,30}$")


class InstagramError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, subcode: int | None = None,
                 transient: bool = False, status: int | None = None):
        super().__init__(message)
        self.code, self.subcode, self.transient, self.status = code, subcode, transient, status


class CredentialsError(InstagramError):
    """Token expired, revoked or invalid. Renew it; retrying will not help."""


class ContainerError(InstagramError):
    """Media container failed (bad image URL/format, expired)."""


@dataclass
class PublishResult:
    media_id: str
    container_id: str | None
    recovered: bool = False  # True when an earlier run had already published this caption


class InstagramClient:
    def __init__(self, *, user_id: str, access_token: str, host: str = "graph.facebook.com",
                 api_version: str = "v21.0", session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 3,
                 poll_interval: float = 10.0, poll_timeout: float = 300.0):
        self.user_id = user_id
        self._token = access_token
        self.base = f"https://{host}/{api_version}"
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_retries = max_retries
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    # ------------------------------------------------------------- plumbing
    def redact(self, text: str) -> str:
        text = str(text)
        if self._token:
            text = text.replace(self._token, "***")
        return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", text)

    def _request(self, method: str, path: str, *, params=None, data=None, retry: bool = True) -> dict:
        url = f"{self.base}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token}"}
        attempts = self.max_retries if retry else 1
        last: InstagramError | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.request(method, url, params=params, json=data, headers=headers, timeout=60)
            except requests.RequestException as exc:
                last = InstagramError(f"network error calling {path}: {self.redact(exc)}", transient=True)
            else:
                last = None
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                if resp.status_code < 400 and isinstance(body, dict) and "error" not in body:
                    return body
                last = self._to_error(path, resp.status_code, body)
            if not last.transient or attempt == attempts:
                break
            wait = min(60, 5 * 2 ** (attempt - 1))
            log.warning("transient error (%s), retry %d/%d in %ss", last, attempt, attempts - 1, wait)
            self.sleep(wait)
        assert last is not None
        raise last

    def _to_error(self, path: str, status: int, body) -> InstagramError:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        code = err.get("code") if isinstance(err.get("code"), int) else None
        sub = err.get("error_subcode") if isinstance(err.get("error_subcode"), int) else None
        msg = self.redact(err.get("message") or f"HTTP {status} with unexpected body")
        detail = self.redact(err.get("error_user_msg") or "")
        text = f"{path}: {msg}" + (f" ({detail})" if detail else "") + f" [code={code} subcode={sub} http={status}]"
        if code in AUTH_CODES or status == 401:
            return CredentialsError(
                text + ". The access token is invalid or expired; renew it (README > Renewing tokens).",
                code=code, subcode=sub, status=status)
        transient = bool(err.get("is_transient")) or code in TRANSIENT_CODES or status >= 500 or status == 429
        if code in PERMISSION_CODES and not transient:
            text += ". Check permissions (instagram_content_publish) and parameters."
        return InstagramError(text, code=code, subcode=sub, transient=transient, status=status)

    @staticmethod
    def _require_id(body: dict, key: str = "id") -> str:
        val = body.get(key)
        if not isinstance(val, str) or not _ID_RE.match(val):
            raise InstagramError(f"unexpected API response: missing/invalid '{key}'")
        return val

    # ------------------------------------------------------------- API calls
    def verify_credentials(self) -> dict:
        """Cheap call that fails fast with CredentialsError if the token is bad."""
        body = self._request("GET", self.user_id, params={"fields": "id,username"})
        if body.get("id") != self.user_id:
            raise CredentialsError("token is valid but does not belong to IG_USER_ID")
        return body

    def publishing_limit(self) -> int | None:
        body = self._request("GET", f"{self.user_id}/content_publishing_limit", params={"fields": "quota_usage"})
        data = body.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            usage = data[0].get("quota_usage")
            return usage if isinstance(usage, int) else None
        return None

    def recent_media(self, limit: int = 15) -> list[dict]:
        body = self._request("GET", f"{self.user_id}/media",
                             params={"fields": "id,caption,timestamp", "limit": str(limit)})
        data = body.get("data")
        if not isinstance(data, list):
            raise InstagramError("unexpected API response for recent media")
        return [m for m in data if isinstance(m, dict)]

    def find_existing(self, caption: str) -> str | None:
        """Idempotency guard: return the media id if this exact caption is already live."""
        want = caption.strip()
        for m in self.recent_media():
            if isinstance(m.get("caption"), str) and m["caption"].strip() == want:
                return self._require_id(m)
        return None

    def create_container(self, image_url: str, caption: str, alt_text: str | None = None) -> str:
        data = {"image_url": image_url, "caption": caption}
        if alt_text:
            data["alt_text"] = alt_text[:1000]
        # Container creation has no side effects on the feed, so retrying is safe.
        return self._require_id(self._request("POST", f"{self.user_id}/media", data=data))

    def wait_until_ready(self, container_id: str) -> str:
        waited = 0.0
        while True:
            status = self._request("GET", container_id, params={"fields": "status_code"}).get("status_code")
            if status in ("FINISHED", "PUBLISHED"):
                return status
            if status in ("ERROR", "EXPIRED"):
                raise ContainerError(f"container {container_id} status {status}: check that the image URL "
                                     "is a public baseline JPEG")
            if status not in ("IN_PROGRESS", None):
                raise InstagramError(f"unexpected container status {status!r}")
            if waited >= self.poll_timeout:
                raise ContainerError(f"container {container_id} not ready after {int(waited)}s", transient=True)
            self.sleep(self.poll_interval)
            waited += self.poll_interval

    def publish_container(self, container_id: str) -> str:
        """media_publish is not blindly retried: on a transient failure we check whether the
        container was in fact published before trying again (avoids double posts)."""
        for attempt in range(1, self.max_retries + 1):
            try:
                body = self._request("POST", f"{self.user_id}/media_publish",
                                     data={"creation_id": container_id}, retry=False)
                return self._require_id(body)
            except InstagramError as exc:
                if not exc.transient or attempt == self.max_retries:
                    raise
                log.warning("media_publish failed transiently (%s); checking container state", exc)
                self.sleep(min(60, 5 * 2 ** (attempt - 1)))
                status = self._request("GET", container_id, params={"fields": "status_code"}).get("status_code")
                if status == "PUBLISHED":
                    raise InstagramError("container was published but media id is unknown; the next run "
                                         "will reconcile it via the recent-media check", transient=False)
        raise InstagramError("unreachable")

    def publish_image(self, image_url: str, caption: str, alt_text: str | None = None) -> PublishResult:
        existing = self.find_existing(caption)
        if existing:
            log.info("caption already live as media %s; recording instead of re-publishing", existing)
            return PublishResult(media_id=existing, container_id=None, recovered=True)
        container = self.create_container(image_url, caption, alt_text)
        self.wait_until_ready(container)
        media_id = self.publish_container(container)
        return PublishResult(media_id=media_id, container_id=container)
