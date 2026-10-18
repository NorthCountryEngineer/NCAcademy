"""Independent synthetic adversarial integration review; no Google traffic."""
import hashlib

from sqlalchemy import select

from academy.classroom import ACCOUNT, FLOW_COOKIE, LINK, OPERATION
from academy.db import Audit, Flow, Record
from academy.google_classroom_transport import GoogleClassroomFailure
from conftest import login
from test_classroom import BASE, complete, connect, google, link_course, start


def test_google_denial_consumes_flow_and_seals_operation(client, app, google):
    headers = login(client)
    state = start(client, headers, "review-denied-connection")
    response = complete(client, state, error="access_denied")
    assert response.status_code == 303 and "classroom_status=error" in response.headers["location"]
    with app.state.database.factory() as db:
        assert db.get(Flow, hashlib.sha256(state.encode()).hexdigest()) is None
        operation = db.scalar(select(Record).where(Record.kind == OPERATION))
        assert operation.value["status"] == "withheld"
        assert operation.value["markers"] == []
    client.cookies.set(FLOW_COOKIE, state)
    complete(client, state)
    assert not any(call[0] == "exchange" for call in google.calls)


def test_disconnect_erases_all_reversible_google_ids_even_in_operations(client, app, google):
    headers = connect(client, google)
    assert link_course(client, headers, google, key_value="review-link-erasure").status_code == 200
    assert client.post(BASE + "/disconnect", json={}, headers=headers).status_code == 200
    with app.state.database.factory() as db:
        rows = list(db.scalars(select(Record).where(Record.kind.in_([ACCOUNT, LINK, OPERATION]))))
        for row in rows:
            value = row.value
            assert not value.get("request_cipher"), "Immutable request must retain only an irreversible keyed fingerprint"
            assert not value.get("cipher"), "Disconnected mutable account/link must erase its envelope"
        payload = " ".join(db.scalars(select(Audit.payload)))
        assert google.google_id not in payload and google.subject not in payload


def test_repeated_disconnect_does_not_misreport_unknown_google_revocation(client, app, google):
    headers = connect(client, google)
    def outage(kind):
        if kind == "revoke":
            raise GoogleClassroomFailure("unavailable", uncertain=True)
    google.on_call = outage
    first = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert first.status_code == 200 and not first.json()["revoked"] and first.json()["uncertain"]
    calls = len(google.calls)
    repeated = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert repeated.status_code == 200
    assert not repeated.json()["revoked"] and repeated.json()["uncertain"]
    assert len(google.calls) == calls


def test_access_revoked_between_membership_check_and_assignment_fetch_withholds(client, app, google):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    def revoked(kind):
        if kind == "courses":
            with app.state.database.write() as db:
                row = db.scalar(select(Record).where(Record.kind == LINK))
                row.set({**row.value, "version": "revoked-before-assignment-read", "cipher": ""})
    google.on_call = revoked
    calls = len([item for item in google.calls if item[0] == "coursework"])
    response = client.get(BASE + "/coursework")
    assert response.status_code in {403, 409}
    assert "Private assignment" not in response.text
    assert len([item for item in google.calls if item[0] == "coursework"]) == calls
