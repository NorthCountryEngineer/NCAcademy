"""Synthetic read-only Google wire fixtures. No live Google calls or accounts."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from academy.google_classroom_transport import (API_URL, AUTHORIZE_URL, MAX_BODY_BYTES,
    REVOKE_URL, SCOPES, TOKEN_URL, USERINFO_URL, GoogleClassroomFailure,
    GoogleClassroomTransport, safe_classroom_link)


ACCESS = "synthetic-access-token-not-live"
REFRESH = "synthetic-refresh-token-not-live"
SECRET = "synthetic-client-secret-not-live"
CODE = "synthetic-auth-code-not-live"
CLIENT = "synthetic-client.apps.googleusercontent.com"
VERIFIER = "synthetic-verifier-" * 4
CHALLENGE = "A" * 43
STATE = "S" * 43
NATIVE = "https://classroom.google.com/c/synthetic-course"


def course(**changes):
    return {"id": "course-123", "name": "Synthetic Earth Science", "section": "Grade 10",
            "courseState": "ACTIVE", "alternateLink": NATIVE, **changes}


def work(**changes):
    return {"id": "work-123", "courseId": "course-123", "title": "Synthetic rock cycle task",
            "description": "Explain observations using your own work.", "state": "PUBLISHED",
            "alternateLink": NATIVE + "/a/synthetic-assignment/details", "workType": "ASSIGNMENT",
            "dueDate": {"year": 2026, "month": 10, "day": 19}, "dueTime": {"hours": 23, "minutes": 59}, **changes}


def tokens(**changes):
    return {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600,
            "scope": " ".join(SCOPES), "token_type": "Bearer", "id_token": "never-used-unverified-jwt", **changes}


@pytest.fixture
def wire():
    state = {"calls": [], "status": 200, "value": {"courses": [course()]}, "headers": {}}
    def respond(request):
        state["calls"].append(request)
        if state.get("error"):
            raise httpx.ReadTimeout(SECRET + ACCESS, request=request)
        if "raw" in state:
            return httpx.Response(state["status"], content=state["raw"],
                headers={"content-type": "application/json", **state["headers"]})
        return httpx.Response(state["status"], json=state["value"], headers=state["headers"])
    settings = SimpleNamespace(auth_mode="core", app_url="https://academy.example.test",
        google_classroom_enabled=True, google_classroom_client_id=CLIENT, google_classroom_client_secret=SECRET)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False,
        cookies={"private-cookie": SECRET}, auth=("inherited", SECRET), headers={"X-Private": SECRET}) as client:
        yield GoogleClassroomTransport(settings, client), state, settings


def failure(call, code=None, *, uncertain=None, reconnect=None):
    with pytest.raises(GoogleClassroomFailure) as captured:
        call()
    error = captured.value
    for secret in (SECRET, ACCESS, REFRESH, CODE):
        assert secret not in str(error) and secret not in repr(error)
        if error.__context__ is not None:
            assert secret not in str(error.__context__)
    if code is not None:
        assert error.code == code
    if uncertain is not None:
        assert error.uncertain is uncertain
    if reconnect is not None:
        assert error.reconnect is reconnect
    return error


def test_authorization_exact_scopes_pkce_and_fixed_callback_no_http(wire):
    adapter, state, _ = wire
    value = adapter.authorize_url(STATE, CHALLENGE)
    parsed = urlsplit(value)
    assert value.startswith(AUTHORIZE_URL + "?")
    query = parse_qs(parsed.query)
    assert query == {"client_id": [CLIENT], "redirect_uri": ["https://academy.example.test/api/google-classroom/callback"],
        "response_type": ["code"], "scope": [" ".join(SCOPES)], "access_type": ["offline"],
        "prompt": ["consent select_account"], "include_granted_scopes": ["false"], "state": [STATE],
        "code_challenge": [CHALLENGE], "code_challenge_method": ["S256"]}
    assert not state["calls"]
    assert SECRET not in value


@pytest.mark.parametrize("state,challenge", [("short", CHALLENGE), (STATE, "short"), (STATE + "\n", CHALLENGE), ([], CHALLENGE)])
def test_invalid_oauth_inputs_do_not_send(wire, state, challenge):
    adapter, wire_state, _ = wire
    failure(lambda: adapter.authorize_url(state, challenge), "invalid_request")
    assert not wire_state["calls"]


def test_exchange_post_body_only_filters_id_token_and_exact_scope(wire):
    adapter, state, _ = wire
    state["value"] = tokens()
    result = adapter.exchange(CODE, VERIFIER)
    assert result == {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600, "scope": list(SCOPES)}
    request = state["calls"][0]
    assert request.method == "POST" and str(request.url) == TOKEN_URL
    assert parse_qs(request.content.decode()) == {"client_id": [CLIENT], "client_secret": [SECRET], "code": [CODE],
        "code_verifier": [VERIFIER], "redirect_uri": ["https://academy.example.test/api/google-classroom/callback"],
        "grant_type": ["authorization_code"]}
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert "x-private" not in request.headers


def test_refresh_has_no_auth_code_and_can_inherit_admitted_scope(wire):
    adapter, state, _ = wire
    state["value"] = {"access_token": ACCESS, "token_type": "Bearer", "expires_in": 7200}
    result = adapter.refresh(REFRESH)
    assert result["refresh_token"] is None and result["scope"] == list(SCOPES)
    assert parse_qs(state["calls"][0].content.decode()) == {"client_id": [CLIENT], "client_secret": [SECRET],
        "refresh_token": [REFRESH], "grant_type": ["refresh_token"]}


@pytest.mark.parametrize("changes", [
    {"access_token": ""}, {"access_token": "header\ninjection"}, {"token_type": "MAC"}, {"token_type": []},
    {"expires_in": True}, {"expires_in": 0}, {"expires_in": 86401}, {"refresh_token": ""},
    {"scope": "openid"}, {"scope": " ".join(SCOPES) + " https://www.googleapis.com/auth/drive"},
    {"scope": " ".join(SCOPES) + " openid"}, {"scope": None}, {"scope": []},
])
def test_invalid_or_expanded_grants_never_admitted(wire, changes):
    adapter, state, _ = wire
    state["value"] = tokens(**changes)
    failure(lambda: adapter.exchange(CODE, VERIFIER), "invalid_response", uncertain=True)
    assert len(state["calls"]) == 1


def test_userinfo_only_returns_stable_subject_and_discards_private_fields(wire):
    adapter, state, _ = wire
    state["value"] = {"sub": "synthetic-google-subject", "email": "synthetic@example.test", "name": "Private name", "picture": "https://bad.example.test/image"}
    assert adapter.userinfo(ACCESS) == {"sub": "synthetic-google-subject"}
    request = state["calls"][0]
    assert str(request.url) == USERINFO_URL and request.headers["authorization"] == "Bearer " + ACCESS
    assert "cookie" not in request.headers and "x-private" not in request.headers


@pytest.mark.parametrize("sub", [None, "", [], "a" * 256, "hello\nworld", "\ud800"])
def test_bad_google_subject_is_withheld(wire, sub):
    adapter, state, _ = wire
    state["raw"] = json.dumps({"sub": sub}).encode()
    failure(lambda: adapter.userinfo(ACCESS), "invalid_response")


def test_courses_are_student_active_fixed_page_projection_and_continuation(wire):
    adapter, state, _ = wire
    state["value"] = {"courses": [course(ownerId="private-owner", enrollmentCode="private-enrollment-code")], "nextPageToken": "opaque-next-123"}
    result = adapter.courses(ACCESS, "opaque-current-123")
    assert result == {"courses": [{"id": "course-123", "name": "Synthetic Earth Science", "section": "Grade 10", "alternate_link": NATIVE}], "next_page_token": "opaque-next-123"}
    request = state["calls"][0]
    assert request.url.path == "/v1/courses"
    assert dict(request.url.params) == {"studentId": "me", "courseStates": "ACTIVE", "pageSize": "100",
        "fields": "courses(id,name,section,courseState,alternateLink),nextPageToken", "pageToken": "opaque-current-123"}
    assert "teacherId" not in request.url.params


@pytest.mark.parametrize("changes", [{"id": "../other"}, {"id": ""}, {"id": []}, {"name": []}, {"name": "\ud800"},
    {"name": "x" * 3001}, {"section": None}, {"courseState": "ARCHIVED"}, {"courseState": None}])
def test_malformed_or_inactive_courses_withheld(wire, changes):
    adapter, state, _ = wire
    state["raw"] = json.dumps({"courses": [course(**changes)]}).encode()
    failure(lambda: adapter.courses(ACCESS), "invalid_response")


@pytest.mark.parametrize("value", [{"courses": [course(), course()]}, {"courses": [course()] * 101},
    {"courses": {}}, {"nextPageToken": ""}, {"nextPageToken": "x" * 2049}, {"nextPageToken": []}])
def test_course_page_limits_duplicates_and_tokens(wire, value):
    adapter, state, _ = wire
    state["value"] = value
    failure(lambda: adapter.courses(ACCESS), "invalid_response")


def test_empty_pages_normalize_without_assuming_membership(wire):
    adapter, state, _ = wire
    state["value"] = {}
    assert adapter.courses(ACCESS) == {"courses": [], "next_page_token": None}
    assert adapter.coursework(ACCESS, "course-123") == {"assignments": [], "next_page_token": None}


def test_coursework_published_only_no_submission_grades_attachments_or_questions(wire):
    adapter, state, _ = wire
    state["value"] = {"courseWork": [work(materials=[{"driveFile": {"id": "private-file"}}],
        maxPoints=100, multipleChoiceQuestion={"choices": ["private-choice"]})]}
    result = adapter.coursework(ACCESS, "course-123")
    assert result == {"assignments": [{"id": "work-123", "course_id": "course-123", "title": "Synthetic rock cycle task",
        "description": "Explain observations using your own work.", "due_date": {"year": 2026, "month": 10, "day": 19},
        "due_time": {"hours": 23, "minutes": 59}, "alternate_link": NATIVE + "/a/synthetic-assignment/details", "work_type": "ASSIGNMENT"}], "next_page_token": None}
    request = state["calls"][0]
    assert request.method == "GET" and request.url.path == "/v1/courses/course-123/courseWork"
    assert dict(request.url.params) == {"courseWorkStates": "PUBLISHED", "pageSize": "50", "orderBy": "updateTime desc",
        "fields": "courseWork(id,courseId,title,description,state,alternateLink,workType,dueDate,dueTime),nextPageToken"}


@pytest.mark.parametrize("changes", [{"courseId": "other"}, {"title": "\ud800"}, {"description": "x" * 30001},
    {"description": "private\x00text"}, {"state": "DRAFT"}, {"workType": []}, {"workType": "UNSPECIFIED"},
    {"dueDate": {"year": 2026, "month": 2, "day": 30}}, {"dueDate": {"year": True, "month": 2, "day": 3}},
    {"dueDate": {"year": 2026, "month": 10}}, {"dueTime": {"hours": 24}}, {"dueTime": {"minutes": True}},
    {"dueTime": {"unknown": 0}}, {"dueTime": {"nanos": -1}}, {"dueDate": None, "dueTime": {"hours": 9}}])
def test_coursework_scope_text_date_time_fail_closed(wire, changes):
    adapter, state, _ = wire
    state["raw"] = json.dumps({"courseWork": [work(**changes)]}).encode()
    failure(lambda: adapter.coursework(ACCESS, "course-123"), "invalid_response")


@pytest.mark.parametrize("link", ["https://classroom.google.com/c/id", "https://classroom.google.com/c/id/a/id/details"])
def test_safe_native_links(link):
    assert safe_classroom_link(link) == link


@pytest.mark.parametrize("link", [None, [], "javascript:alert(1)", "http://classroom.google.com/c/id",
    "https://classroom.google.com.evil.test/c/id", "https://classroom.google.com@evil.test/c/id",
    "https://classroom.google.com:443/c/id", "https://CLASSROOM.google.com/c/id", "https://classroom.google.com//evil.test/id",
    "https://classroom.google.com/c/id?redirect=https://evil.test", "https://classroom.google.com/c/id#fragment",
    "https://classroom.google.com/c/id\n", "https://classroom.google.com/c/%0aid", "https://classroom.google.com/c/%2fid",
    "https://classroom.google.com/c/\\evil", "https://classroom.google.com/c/élève"])
def test_unsafe_native_link_is_dropped(link):
    assert safe_classroom_link(link) is None


def test_bad_native_links_do_not_poison_assignment_but_never_escape(wire):
    adapter, state, _ = wire
    state["value"] = {"courseWork": [work(alternateLink="https://evil.example.test")]}
    assert adapter.coursework(ACCESS, "course-123")["assignments"][0]["alternate_link"] is None


@pytest.mark.parametrize("status,code,reconnect", [(400,"denied",True),(401,"denied",True),(403,"denied",False),
    (404,"denied",False),(429,"unavailable",False),(500,"unavailable",False),(302,"unavailable",False)])
def test_sanitized_token_failures_no_retry_or_redirect(wire, status, code, reconnect):
    adapter, state, _ = wire
    state.update(status=status, raw=(SECRET + ACCESS).encode(), headers={"Location": "https://evil.test"})
    error = failure(lambda: adapter.exchange(CODE, VERIFIER), code, reconnect=reconnect)
    assert error.uncertain == (status in {500,302})
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("raw,headers", [(b'{"sub":"a","sub":"b"}', {}), (b'{"sub":NaN}', {}), (b'[]', {}),
    (b'{}', {"content-type":"text/html"}), (b'{}', {"content-length":str(MAX_BODY_BYTES+1)}),
    (b'{}', {"content-length":"unknown"}), (b'x'*(MAX_BODY_BYTES+1), {}), (b'\xff', {})])
def test_bounded_duplicate_rejecting_json_parser(wire, raw, headers):
    adapter, state, _ = wire
    state.update(raw=raw, headers=headers)
    failure(lambda: adapter.userinfo(ACCESS))
    assert len(state["calls"]) == 1


def test_network_exception_is_sanitized_without_context_or_automatic_retry(wire):
    adapter, state, _ = wire
    state["error"] = True
    error = failure(lambda: adapter.refresh(REFRESH), "unavailable", uncertain=True)
    assert error.__context__ is None and error.__cause__ is None
    assert len(state["calls"]) == 1


def test_revoke_post_no_query_no_retained_response(wire):
    adapter, state, _ = wire
    state["raw"] = b""
    assert adapter.revoke(REFRESH) is True
    request = state["calls"][0]
    assert str(request.url) == REVOKE_URL and request.method == "POST"
    assert parse_qs(request.content.decode()) == {"token": [REFRESH]}
    assert "authorization" not in request.headers and "cookie" not in request.headers


@pytest.mark.parametrize("field,value", [("auth_mode","local"), ("google_classroom_enabled",False),
    ("google_classroom_enabled","true"), ("google_classroom_client_id",""),
    ("google_classroom_client_secret","bad\nsecret"), ("app_url","http://localhost:8090"),
    ("app_url","https://academy.example.test/path"), ("app_url","https://user@academy.example.test"),
    ("app_url","https://academy.example.test?bad=true")])
def test_unconfigured_or_local_gate_never_calls_google(wire, field, value):
    adapter, state, settings = wire
    setattr(settings, field, value)
    failure(lambda: adapter.courses(ACCESS), "disabled")
    assert not state["calls"]


@pytest.mark.parametrize("call", [lambda a: a.courses("bad\ntoken"), lambda a: a.courses(ACCESS, ""),
    lambda a: a.courses(ACCESS, "x" * 2049), lambda a: a.coursework(ACCESS, "../courses/other"),
    lambda a: a.exchange(CODE, "short"), lambda a: a.refresh(""), lambda a: a.revoke(""),
    lambda a: a._request("POST", API_URL + "/courses", data={}),
    lambda a: a._request("GET", "https://evil.test/courses")])
def test_invalid_request_or_arbitrary_endpoint_never_sent(wire, call):
    adapter, state, _ = wire
    failure(lambda: call(adapter), "invalid_request")
    assert not state["calls"]
