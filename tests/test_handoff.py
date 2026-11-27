"""Handoff delivery: the team email leads with the summary, carries the masked transcript and the reply address."""
import json

from stepchaser.handoff import MailHandoffSink, QueueHandoffSink, render_email
from stepchaser.mail import MemoryMailer

PACKAGE = {"id": 7, "reason": "personal_account", "route_suggestion": "tax_preparation",
           "summary": "Jo asked: \"where is my refund\". Suggested service: tax preparation. Handed off because: personal account.",
           "transcript": [{"role": "visitor", "text": "where is my refund, call me on [PHONE]"}, {"role": "assistant", "text": "Passed on."}],
           "contact": {"name": "Jo Sample", "email": "jo@example.test"}}


def test_email_has_summary_first_then_reply_address_then_transcript():
    subject, body = render_email(PACKAGE)
    assert subject == "Handoff #7: Jo Sample (personal account)"
    lines = body.splitlines()
    assert lines[0] == PACKAGE["summary"] and "Reply to: jo@example.test" in body
    assert body.index("visitor: where is my refund") > body.index("Transcript")


def test_email_without_a_contact_says_so():
    pkg = {**PACKAGE, "contact": {"name": "", "email": ""}}
    subject, body = render_email(pkg)
    assert "a visitor" in subject and "no email given" in body


def test_mail_sink_sends_one_email_to_the_team():
    mail = MemoryMailer()
    MailHandoffSink(mail, "team@example.test").deliver(PACKAGE)
    assert len(mail.outbox) == 1 and mail.outbox[0].to == "team@example.test" and mail.outbox[0].subject.startswith("Handoff #7")


def test_queue_sink_keeps_the_list_and_a_json_lines_file(tmp_path):
    path = tmp_path / "q" / "handoffs.jsonl"
    sink = QueueHandoffSink(path)
    sink.deliver(PACKAGE)
    sink.deliver({**PACKAGE, "id": 8})
    assert [p["id"] for p in sink.items] == [7, 8]
    assert [json.loads(x)["id"] for x in path.read_text(encoding="utf-8").splitlines()] == [7, 8]
