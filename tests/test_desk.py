"""The desk service in process: auth on the ledger routes, chat with handoff, bookings, and the clock."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from stepchaser.desk import Desk, make_handler
from stepchaser.mail import MemoryMailer

KEY = "test-key"


@pytest.fixture
def desk(tmp_path):
    mail = MemoryMailer()
    d = Desk({"DATA_DIR": str(tmp_path), "DESK_KEY": KEY, "REMIND_EVERY_SECONDS": "3600", "N8N_BASE": "http://127.0.0.1:9"}, mailer=mail)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d.base, d.mail = f"http://127.0.0.1:{srv.server_port}", mail
    yield d
    srv.shutdown()


def call(desk, method, path, body=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Desk-Key"] = key
    req = urllib.request.Request(desk.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_ledger_routes_need_the_key(desk):
    assert call(desk, "POST", "/ledger/register", {"idempotency_key": "k", "name": "A", "email": "a@example.test"})[0] == 401
    assert call(desk, "POST", "/ledger/register", {"idempotency_key": "k", "name": "A", "email": "a@example.test"}, key="wrong")[0] == 401
    assert call(desk, "GET", "/ledger/partners")[0] == 401


def test_register_twice_item_and_chase_flow(desk):
    reg = {"idempotency_key": "k1", "name": "Sam", "email": "sam@example.test"}
    c, out = call(desk, "POST", "/ledger/register", reg, KEY)
    assert c == 200 and out["created"] and not out["duplicate"]
    c, out = call(desk, "POST", "/ledger/register", reg, KEY)
    assert out["duplicate"] and not out["created"]
    assert call(desk, "POST", "/ledger/register", {"idempotency_key": "k2", "name": "", "email": "x"}, KEY)[0] == 422
    c, out = call(desk, "POST", "/ledger/item", {"email": "sam@example.test", "item": "Profile photo"}, KEY)
    assert c == 200 and len(out["partner"]["missing"]) == 4
    assert call(desk, "POST", "/ledger/item", {"email": "ghost@example.test", "item": "Profile photo"}, KEY)[0] == 404
    assert call(desk, "POST", "/ledger/chase", {}, KEY)[1]["chases"] == []
    call(desk, "POST", "/api/clock", {"advance_minutes": 61})
    chases = call(desk, "POST", "/ledger/chase", {}, KEY)[1]["chases"]
    assert len(chases) == 1 and chases[0]["partner"]["email"] == "sam@example.test"
    assert call(desk, "POST", "/ledger/chase", {}, KEY)[1]["chases"] == []
    assert call(desk, "POST", "/ledger/release", {"partner_id": chases[0]["partner"]["id"]}, KEY)[0] == 200
    assert len(call(desk, "POST", "/ledger/chase", {}, KEY)[1]["chases"]) == 1


def test_chat_routes_answers_and_hands_off_with_masking(desk):
    c, out = call(desk, "POST", "/api/chat", {"session": "s1", "message": "how much does credit repair cost?", "name": "Jo", "email": "jo@example.test"})
    assert c == 200 and out["route"] == "credit_repair" and not out["handed_off"]          # the stand-in classifier here
    c, out = call(desk, "POST", "/api/chat", {"session": "s1", "message": "my ssn is 123-45-6789, can I speak to a person"})
    assert out["handed_off"] and "SSN" in out["kinds"] and "123-45-6789" not in json.dumps(out)
    team = [m for m in desk.mail.outbox if m.subject.startswith("Handoff #")]
    assert len(team) == 1 and "[SSN]" in team[0].body and "123-45-6789" not in team[0].body and "jo@example.test" in team[0].body
    c, out = call(desk, "POST", "/api/chat", {"session": "s1", "message": "hello?"})
    assert out["reason"] == "already_handed_off" and len([m for m in desk.mail.outbox if m.subject.startswith("Handoff #")]) == 1


def test_bookings_reschedule_cancel_and_clock(desk):
    c, b = call(desk, "POST", "/api/bookings", {"name": "Jo", "email": "jo@example.test", "local_time": "2030-01-10T10:00", "tz": "America/New_York"})
    assert c == 200 and b["start_utc"] == "2030-01-10T15:00:00Z"
    assert call(desk, "POST", "/api/bookings", {"name": "Jo", "email": "jo@example.test", "local_time": "2030-03-10T02:30", "tz": "America/New_York"})[0] == 422
    c, b2 = call(desk, "POST", f"/api/bookings/{b['id']}/reschedule", {"local_time": "2030-01-11T10:00"})
    assert b2["start_utc"] == "2030-01-11T15:00:00Z"
    c, b3 = call(desk, "POST", f"/api/bookings/{b['id']}/cancel")
    assert b3["status"] == "cancelled"
    assert call(desk, "POST", f"/api/bookings/{b['id']}/reschedule", {"local_time": "2030-01-12T10:00"})[0] == 422
    assert call(desk, "POST", "/api/bookings/999/cancel")[0] in (404, 422)
    subjects = [m.subject.split(":")[0] for m in desk.mail.outbox]
    assert subjects == ["Booked", "Moved", "Cancelled"]


def test_state_page_and_unknown_routes(desk):
    c, s = call(desk, "GET", "/api/state")
    assert c == 200 and "keyword stand-in" in s["classifier"] and len(s["required_items"]) == 5
    with urllib.request.urlopen(desk.base + "/") as r:
        assert b"Stepchaser desk" in r.read()
    assert call(desk, "GET", "/nope")[0] == 404
    assert call(desk, "POST", "/api/nope", {})[0] == 404


def test_signup_forward_reports_a_down_workflow_engine(desk):
    c, out = call(desk, "POST", "/api/signup", {"name": "A", "email": "a@example.test"})
    assert c == 502 and "n8n" in out["error"]
