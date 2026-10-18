"""Fixed-endpoint, student-owned, read-only Google Classroom adapter.

No assignment text, token, Google identity or response body belongs in an audit
event or model prompt. The caller owns current local access checks, encrypted
credentials and durable operation markers. This adapter never retries a call.
"""
import json
import re
import time
from datetime import date
from urllib.parse import urlencode, urlsplit

import httpx


SCOPES = ("openid", "https://www.googleapis.com/auth/classroom.courses.readonly",
          "https://www.googleapis.com/auth/classroom.coursework.me.readonly")
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://classroom.googleapis.com/v1"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 25


class GoogleClassroomFailure(Exception):
    """Static metadata only; never retains provider bodies or credentials."""

    def __init__(self, code, *, status_code=503, uncertain=False, reconnect=False):
        self.code = code
        self.status_code = status_code
        self.uncertain = bool(uncertain)
        self.reconnect = bool(reconnect)
        super().__init__("Google Classroom request could not be admitted or verified")


def _text(value, maximum, *, blank=False):
    if not isinstance(value, str) or not blank and not value.strip():
        return False
    try:
        return (len(value.encode("utf-8")) <= maximum
                and not any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value))
    except UnicodeError:
        return False


def _opaque(value, maximum=4096):
    return isinstance(value, str) and bool(re.fullmatch(r"[\x21-\x7e]{1," + str(maximum) + r"}", value))


def _id(value):
    # Classroom-assigned identifiers only. Aliases and path fragments are never
    # accepted as upstream path components.
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value))


def safe_classroom_link(value):
    """Only native Classroom HTTPS links; never fetch the link or its assets."""
    if not isinstance(value, str) or len(value) > 2048 or not value.isascii():
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.netloc != "classroom.google.com"
                or parsed.username is not None or parsed.password is not None
                or not parsed.path.startswith("/") or parsed.path.startswith("//")
                or re.search(r"[\x00-\x20\x7f\\]", value)
                or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f|5c|2f)", value, re.I)
                or parsed.fragment or parsed.query):
            return None
        return value
    except ValueError:
        return None


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError()
        result[name] = value
    return result


class GoogleClassroomTransport:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http if http is not None else httpx.Client(trust_env=False, follow_redirects=False)

    def close(self):
        self.http.close()

    def _configuration(self):
        settings = self.settings
        app_url = getattr(settings, "app_url", "")
        client_id = getattr(settings, "google_classroom_client_id", "")
        secret = getattr(settings, "google_classroom_client_secret", "")
        try:
            origin = urlsplit(app_url)
            valid_origin = (origin.scheme == "https" and origin.netloc == origin.hostname
                            and bool(origin.hostname) and origin.path == "" and not origin.query and not origin.fragment)
        except (ValueError, TypeError):
            valid_origin = False
        if (getattr(settings, "auth_mode", None) != "core"
                or getattr(settings, "google_classroom_enabled", False) is not True
                or not valid_origin or not _opaque(client_id, 255) or not client_id.endswith(".apps.googleusercontent.com")
                or not _opaque(secret, 4096)):
            raise GoogleClassroomFailure("disabled")
        return client_id, secret, app_url + "/api/google-classroom/callback"

    def authorize_url(self, state, code_challenge):
        client_id, _, callback = self._configuration()
        if (not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state)
                or not isinstance(code_challenge, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge)):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        return AUTHORIZE_URL + "?" + urlencode({"client_id": client_id, "redirect_uri": callback,
            "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline",
            "prompt": "consent select_account", "include_granted_scopes": "false", "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256"})

    def _request(self, method, url, *, access_token=None, params=None, data=None, revoke=False):
        self._configuration()
        allowed = (method == "POST" and url in {TOKEN_URL, REVOKE_URL}
                   or method == "GET" and url in {USERINFO_URL, API_URL + "/courses"}
                   or method == "GET" and re.fullmatch(re.escape(API_URL) + r"/courses/[A-Za-z0-9_-]{1,160}/courseWork", url))
        if not allowed:
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        if access_token is not None and not _opaque(access_token):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        mutation = method == "POST"
        started = time.monotonic()
        response = None
        try:
            request = self.http.build_request(method, url, params=params, data=data,
                timeout=httpx.Timeout(15, connect=3, pool=1))
            # Discard inherited client cookies, auth, headers and URL credentials.
            headers = {"Accept": "application/json", "User-Agent": "NCAcademy-Classroom/1"}
            if access_token is not None:
                headers["Authorization"] = "Bearer " + access_token
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                headers["Content-Length"] = str(len(request.content))
            headers["Host"] = request.url.netloc.decode("ascii")
            request.headers = httpx.Headers(headers)
            response = self.http.send(request, auth=None, follow_redirects=False, stream=True)
            status = response.status_code
            if status != 200:
                if status in {401, 403, 404}:
                    raise GoogleClassroomFailure("denied", status_code=403, reconnect=status == 401)
                if status in {400, 409, 413, 415, 422}:
                    # Token errors cannot safely expose their description. A
                    # rejected token exchange or refresh requires reconnection.
                    raise GoogleClassroomFailure("denied" if url == TOKEN_URL else "invalid_request",
                        status_code=403 if url == TOKEN_URL else 422, reconnect=url == TOKEN_URL)
                raise GoogleClassroomFailure("unavailable", uncertain=mutation and status != 429)
            if revoke:
                # Google documents an empty successful revocation body. Do not
                # parse or retain provider text returned on this path.
                return True
            declared = response.headers.get("content-length")
            if (response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                    or declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES)):
                raise GoogleClassroomFailure("invalid_response", uncertain=mutation)
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES or time.monotonic() - started > MAX_SECONDS:
                    raise GoogleClassroomFailure("invalid_response", uncertain=mutation)
            result = json.loads(bytes(body).decode("utf-8"), object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except GoogleClassroomFailure:
            raise
        except (httpx.HTTPError, OSError, ValueError, UnicodeError, RecursionError):
            pass
        finally:
            if response is not None:
                response.close()
        # Raise outside the handler to avoid retaining credential-bearing HTTP
        # exceptions in __context__, even when tracebacks are inspected.
        raise GoogleClassroomFailure("unavailable", uncertain=mutation) from None

    def _tokens(self, value, *, refresh=False):
        scopes = value.get("scope", " ".join(SCOPES) if refresh else None)
        parsed_scopes = scopes.split() if isinstance(scopes, str) else None
        token_type = value.get("token_type")
        if (not _opaque(value.get("access_token")) or not isinstance(token_type, str) or token_type.lower() != "bearer"
                or type(value.get("expires_in")) is not int or not 1 <= value["expires_in"] <= 86400
                or value.get("refresh_token") is not None and not _opaque(value["refresh_token"])
                or parsed_scopes is None or len(parsed_scopes) != len(SCOPES) or set(parsed_scopes) != set(SCOPES)):
            raise GoogleClassroomFailure("invalid_response", uncertain=True)
        return {"access_token": value["access_token"], "refresh_token": value.get("refresh_token"),
                "expires_in": value["expires_in"], "scope": list(SCOPES)}

    def exchange(self, code, verifier):
        client_id, secret, callback = self._configuration()
        if (not _opaque(code, 4096) or not isinstance(verifier, str)
                or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        return self._tokens(self._request("POST", TOKEN_URL, data={"client_id": client_id,
            "client_secret": secret, "code": code, "code_verifier": verifier, "redirect_uri": callback,
            "grant_type": "authorization_code"}))

    def refresh(self, refresh_token):
        client_id, secret, _ = self._configuration()
        if not _opaque(refresh_token):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        return self._tokens(self._request("POST", TOKEN_URL, data={"client_id": client_id,
            "client_secret": secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}), refresh=True)

    def userinfo(self, access_token):
        value = self._request("GET", USERINFO_URL, access_token=access_token)
        sub = value.get("sub")
        if not isinstance(sub, str) or not re.fullmatch(r"[\x21-\x7e]{1,255}", sub):
            raise GoogleClassroomFailure("invalid_response")
        return {"sub": sub}

    def _page_token(self, page_token):
        if page_token is not None and not _opaque(page_token, 2048):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        return {} if page_token is None else {"pageToken": page_token}

    def _rows(self, value, name, maximum):
        rows, token = value.get(name, []), value.get("nextPageToken")
        if (not isinstance(rows, list) or len(rows) > maximum
                or token is not None and not _opaque(token, 2048)):
            raise GoogleClassroomFailure("invalid_response")
        return rows, token

    def courses(self, access_token, page_token=None):
        value = self._request("GET", API_URL + "/courses", access_token=access_token,
            params={"studentId": "me", "courseStates": "ACTIVE", "pageSize": 100,
                    "fields": "courses(id,name,section,courseState,alternateLink),nextPageToken", **self._page_token(page_token)})
        rows, token = self._rows(value, "courses", 100)
        result, seen = [], set()
        for row in rows:
            if (not isinstance(row, dict) or not _id(row.get("id")) or row["id"] in seen
                    or not _text(row.get("name"), 3000) or not _text(row.get("section", ""), 3000, blank=True)
                    or row.get("courseState") != "ACTIVE"):
                raise GoogleClassroomFailure("invalid_response")
            seen.add(row["id"])
            result.append({"id": row["id"], "name": row["name"], "section": row.get("section", ""),
                           "alternate_link": safe_classroom_link(row.get("alternateLink"))})
        return {"courses": result, "next_page_token": token}

    def coursework(self, access_token, course_id, page_token=None):
        if not _id(course_id):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        value = self._request("GET", API_URL + f"/courses/{course_id}/courseWork", access_token=access_token,
            params={"courseWorkStates": "PUBLISHED", "pageSize": 50, "orderBy": "updateTime desc",
                "fields": "courseWork(id,courseId,title,description,state,alternateLink,workType,dueDate,dueTime),nextPageToken",
                **self._page_token(page_token)})
        rows, token = self._rows(value, "courseWork", 50)
        result, seen = [], set()
        for row in rows:
            if (not isinstance(row, dict) or not _id(row.get("id")) or row["id"] in seen or row.get("courseId") != course_id
                    or not _text(row.get("title"), 12000) or not _text(row.get("description", ""), 30000, blank=True)
                    or row.get("state") != "PUBLISHED" or not isinstance(row.get("workType"), str)
                    or row["workType"] not in {"ASSIGNMENT", "SHORT_ANSWER_QUESTION", "MULTIPLE_CHOICE_QUESTION"}):
                raise GoogleClassroomFailure("invalid_response")
            due_date, due_time = row.get("dueDate"), row.get("dueTime")
            if due_date is not None:
                if (not isinstance(due_date, dict) or set(due_date) != {"year", "month", "day"}
                        or any(type(part) is not int for part in due_date.values())):
                    raise GoogleClassroomFailure("invalid_response")
                try:
                    date(due_date["year"], due_date["month"], due_date["day"])
                except (ValueError, OverflowError):
                    raise GoogleClassroomFailure("invalid_response") from None
            if due_time is not None:
                if (due_date is None or not isinstance(due_time, dict) or not set(due_time) <= {"hours", "minutes", "seconds", "nanos"}
                        or any(type(part) is not int for part in due_time.values())
                        or any(not 0 <= due_time.get(part, 0) <= maximum for part, maximum in
                               (("hours", 23), ("minutes", 59), ("seconds", 59), ("nanos", 999999999)))):
                    raise GoogleClassroomFailure("invalid_response")
            seen.add(row["id"])
            result.append({"id": row["id"], "course_id": course_id, "title": row["title"],
                "description": row.get("description", ""), "due_date": due_date, "due_time": due_time,
                "alternate_link": safe_classroom_link(row.get("alternateLink")), "work_type": row["workType"]})
        return {"assignments": result, "next_page_token": token}

    def revoke(self, token):
        if not _opaque(token):
            raise GoogleClassroomFailure("invalid_request", status_code=422)
        return self._request("POST", REVOKE_URL, data={"token": token}, revoke=True)
