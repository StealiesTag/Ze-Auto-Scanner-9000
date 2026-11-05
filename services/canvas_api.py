"""Small HTTPS-only Canvas REST client with guarded pagination and timeouts."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CanvasAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def validate_canvas_base_url(raw_url: str) -> str:
    parsed = urlsplit((raw_url or "").strip().rstrip("/"))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or parsed.port not in {None, 443}):
        raise ValueError("Canvas must use an HTTPS institution URL with no path, query, or embedded credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Canvas URL must use a public institution hostname; local and internal hosts are blocked.")
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise ValueError("Canvas URL cannot target a private or reserved IP address.")
        raise ValueError("Use the institution's Canvas hostname instead of an IP address.")
    except ValueError as error:
        if "Use the institution" in str(error) or "private or reserved" in str(error):
            raise
    if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host or host.startswith(".") or host.endswith("."):
        raise ValueError("Canvas URL contains an invalid institution hostname.")
    return f"https://{host}"


class _SameHostHTTPSRedirect(HTTPRedirectHandler):
    def __init__(self, origin: str):
        super().__init__()
        self.origin = urlsplit(origin)

    def redirect_request(self, request, fp, code, message, headers, new_url):
        target = urlsplit(new_url)
        if target.scheme != "https" or target.hostname != self.origin.hostname or target.port not in {None, 443}:
            return None
        return super().redirect_request(request, fp, code, message, headers, new_url)


class CanvasClient:
    def __init__(self, base_url: str, access_token: str, *, timeout: float = 10.0, opener=None, resolver=None):
        self.base_url = validate_canvas_base_url(base_url)
        self.access_token = access_token
        self.timeout = max(2.0, min(float(timeout), 30.0))
        self._custom_opener = opener is not None
        self.opener = opener or build_opener(_SameHostHTTPSRedirect(self.base_url))
        self.resolver = resolver or socket.getaddrinfo

    def _check_public_target(self, url: str) -> None:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != urlsplit(self.base_url).hostname
                or parsed.port not in {None, 443} or parsed.username or parsed.password):
            raise CanvasAPIError("Canvas redirected outside the configured institution host.")
        if self._custom_opener:
            return
        try:
            addresses = {item[4][0] for item in self.resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise CanvasAPIError("Canvas hostname resolved to a private or reserved network address.")
        except CanvasAPIError:
            raise
        except Exception as error:
            raise CanvasAPIError("Canvas hostname could not be safely resolved.") from error

    def _request(self, url: str, *, method="GET", fields=None) -> tuple[object, object]:
        self._check_public_target(url)
        data = urlencode(fields or {}).encode("utf-8") if fields is not None else None
        request = Request(url, data=data, method=method, headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            **({"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {}),
        })
        max_attempts = 3 if method == "GET" else 1
        for attempt in range(max_attempts):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    if not 200 <= int(response.status) < 300:
                        raise CanvasAPIError(f"Canvas returned HTTP {response.status}.", int(response.status))
                    body = response.read(4 * 1024 * 1024 + 1)
                    if len(body) > 4 * 1024 * 1024:
                        raise CanvasAPIError("Canvas response exceeded the 4 MB safety limit.")
                    headers = response.headers
                return headers, json.loads(body.decode("utf-8")) if body else None
            except HTTPError as error:
                if method == "GET" and error.code in {429, 500, 502, 503, 504} and attempt + 1 < max_attempts:
                    retry_after = error.headers.get("Retry-After", "")
                    try:
                        delay = min(2.0, max(0.1, float(retry_after)))
                    except (TypeError, ValueError):
                        delay = 0.4 * (attempt + 1)
                    time.sleep(delay)
                    continue
                if error.code in {301, 302, 303, 307, 308}:
                    raise CanvasAPIError("Canvas attempted an unsafe or unsupported redirect.", error.code) from error
                message = {
                    401: "Canvas rejected the authorization token. Reconnect Canvas.",
                    403: "Canvas denied this action. Check the teacher's course and grading permissions.",
                    404: "Canvas could not find that course, assignment, or submission.",
                    429: "Canvas rate limit reached. Wait briefly and retry.",
                }.get(error.code, f"Canvas request failed with HTTP {error.code}.")
                raise CanvasAPIError(message, error.code) from error
            except URLError as error:
                raise CanvasAPIError("Canvas could not be reached over HTTPS.") from error
            except (TimeoutError, OSError) as error:
                raise CanvasAPIError("Canvas request timed out or the connection was interrupted.") from error
            except json.JSONDecodeError as error:
                raise CanvasAPIError("Canvas returned an invalid JSON response.") from error
        raise CanvasAPIError("Canvas request failed after safe retries.")

    def get(self, path: str, params=None):
        path = path.lstrip("/")
        if not path.startswith("api/v1/"):
            path = "api/v1/" + path
        url = self.base_url + "/" + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        return self._request(url)[1]

    def paged(self, path: str, params=None) -> list[dict]:
        path = path.lstrip("/")
        if not path.startswith("api/v1/"):
            path = "api/v1/" + path
        url = self.base_url + "/" + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        output = []
        seen = set()
        for _page in range(200):
            if url in seen:
                raise CanvasAPIError("Canvas pagination loop detected.")
            seen.add(url)
            headers, data = self._request(url)
            if not isinstance(data, list):
                raise CanvasAPIError("Canvas returned an unexpected collection response.")
            output.extend(item for item in data if isinstance(item, dict))
            if len(output) > 20_000:
                raise CanvasAPIError("Canvas list exceeded the 20,000-record safety limit.")
            link = headers.get("Link", "")
            next_url = None
            for match in re.finditer(r'<([^>]+)>\s*;\s*rel="([^"]+)"', link):
                if "next" in match.group(2).split():
                    next_url = urljoin(url, match.group(1))
                    break
            if not next_url:
                return output
            self._check_public_target(next_url)
            url = next_url
        raise CanvasAPIError("Canvas exceeded the maximum safe number of pages.")

    def put(self, path: str, fields: dict):
        path = path.lstrip("/")
        if not path.startswith("api/v1/"):
            path = "api/v1/" + path
        return self._request(self.base_url + "/" + path, method="PUT", fields=fields)[1]

    def self_user(self):
        user = self.get("users/self", {"include[]": "avatar_url"})
        if not isinstance(user, dict) or not user.get("id"):
            raise CanvasAPIError("Canvas did not identify the authorized teacher.")
        return user

    def courses(self):
        return self.paged("courses", [("enrollment_type[]", "teacher"), ("enrollment_state[]", "active"), ("per_page", "100")])

    def assignments(self, course_id: str):
        return self.paged(f"courses/{course_id}/assignments", [("per_page", "100")])

    def roster(self, course_id: str):
        return self.paged(f"courses/{course_id}/users", [("enrollment_type[]", "student"), ("include[]", "enrollments"), ("per_page", "100")])

    def assignment_submissions(self, course_id: str, assignment_id: str):
        return self.paged(f"courses/{course_id}/assignments/{assignment_id}/submissions", [("include[]", "user"), ("per_page", "100")])

    def submission(self, course_id: str, assignment_id: str, user_id: str):
        return self.get(f"courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}")

    def post_grade(self, course_id: str, assignment_id: str, user_id: str, grade: str):
        return self.put(f"courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}", {"submission[posted_grade]": grade})
