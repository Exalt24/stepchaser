"""Generate n8n/onboarding.workflow.json. The JSON is written by this script so node names and connections cannot drift.

Three entry points in one workflow:
  Signup   webhook POST /webhook/partner-signup -> ledger (dedupes) -> welcome email + team summary -> reply 202, or 200 duplicate
  Item     webhook POST /webhook/partner-item   -> ledger marks the item -> when the checklist is complete, partner + team emails
  Chase    schedule (every CHASE_SECONDS)       -> ledger hands back the reminders now due -> emails; a failed send releases the claim

The ledger (stepchaser/ledger.py, behind the desk service) holds the state, because a check-then-insert inside the
workflow can race when two deliveries arrive together. n8n owns the orchestration and the wording of every email.
Edit the wording in the three "Compose" Code nodes; edit how often it chases in the Schedule node.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHASE_SECONDS = 15  # the demo polls fast; for real use set the Schedule node to hourly, the ledger decides what is due
FROM = "desk@cedar-lane.example.test"
ledger_headers = {"parameters": [{"name": "X-Desk-Key", "value": "={{ $env.DESK_KEY }}"}]}


def http(node_id, name, path, body_expr, pos):
    return {"id": node_id, "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos,
            "parameters": {"method": "POST", "url": "={{ $env.DESK_URL }}" + path, "sendHeaders": True, "headerParameters": ledger_headers,
                           "sendBody": True, "specifyBody": "json", "jsonBody": body_expr,
                           "options": {"response": {"response": {"fullResponse": True, "neverError": True}}}}}


def cond_if(node_id, name, left, pos, op_type="boolean", operation="true", right=None):
    cond = {"id": node_id + "-c", "leftValue": left, "rightValue": right if right is not None else "",
            "operator": {"type": op_type, "operation": operation, **({"singleValue": True} if op_type == "boolean" else {})}}
    return {"id": node_id, "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos,
            "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                                          "conditions": [cond], "combinator": "and"}, "options": {}}}


def code(node_id, name, js, pos):
    return {"id": node_id, "name": name, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos,
            "parameters": {"mode": "runOnceForAllItems", "jsCode": js}}


def email(node_id, name, pos, on_error=False):
    node = {"id": node_id, "name": name, "type": "n8n-nodes-base.emailSend", "typeVersion": 2.1, "position": pos,
            "parameters": {"fromEmail": FROM, "toEmail": "={{ $json.to }}", "subject": "={{ $json.subject }}", "emailFormat": "text",
                           "text": "={{ $json.text }}", "options": {"appendAttribution": False}},
            "credentials": {"smtp": {"id": "smtp-local", "name": "Local mail catcher"}}}
    if on_error:
        node["onError"] = "continueErrorOutput"
    return node


def respond(node_id, name, body_expr, code_expr, pos, once=True):
    node = {"id": node_id, "name": name, "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": pos,
            "parameters": {"respondWith": "json", "responseBody": body_expr, "options": {"responseCode": code_expr}}}
    if once:
        node["executeOnce"] = True
    return node


COMPOSE_SIGNUP = r"""
// Wording for the two signup emails. The partner and checklist come from the ledger.
const p = $input.first().json.body.partner;
const req = $('Partner signup').first().json.body;
const list = p.missing.map((m, i) => `${i + 1}. ${m}`).join('\n');
const kick = req.kickoff ? `\nYour kickoff call is set for ${req.kickoff} (${p.tz}). A calendar file was created for it.\n` : '';
return [
  { json: { to: p.email, subject: `Welcome, ${p.name}: your next steps`, text:
`Hi ${p.name},

Welcome to the ${p.partner_type} program. To finish onboarding we need ${p.missing.length} items from you:

${list}
${kick}
Reply to this email with each item as you have it. If something is still missing in a couple of days, we will send a short reminder that lists only what is left.

Cedar Lane Services` } },
  { json: { to: $env.TEAM_EMAIL, subject: `New partner signup: ${p.name}`, text:
`${p.name} <${p.email}> signed up as a ${p.partner_type} (time zone ${p.tz}).

Checklist: 0 of ${p.missing.length} done.
${list}

The welcome email with next steps was sent. The ledger will chase what is missing and tell you here if they stall.` } }
];
"""

COMPOSE_DONE = r"""
const p = $input.first().json.body.partner;
return [
  { json: { to: p.email, subject: `You are all set, ${p.name}`, text:
`Hi ${p.name},

We have everything we need. Your onboarding is complete and our team will be in touch about the kickoff.

Cedar Lane Services` } },
  { json: { to: $env.TEAM_EMAIL, subject: `Onboarding complete: ${p.name}`, text:
`${p.name} <${p.email}> has all ${p.done.length} checklist items in. No more reminders will be sent.` } }
];
"""

COMPOSE_CHASE = r"""
// One email per claimed reminder, one alert per stalled partner, and a single digest for the team.
const chases = $input.first().json.body.chases || [];
const out = [];
const digest = [];
for (const c of chases) {
  const p = c.partner;
  const list = c.missing.map(m => `- ${m}`).join('\n');
  if (c.kind === 'stalled') {
    out.push({ json: { partner_id: p.id, kind: 'stalled', to: $env.TEAM_EMAIL, subject: `Stalled onboarding: ${p.name}`, text:
`${p.name} <${p.email}> has not finished after ${c.n} reminders. Still missing:
${list}

No more automatic reminders will go to them. A person should reach out.` } });
    continue;
  }
  const closing = c.last ? 'This is our last automatic reminder. After this, someone from our team will contact you.' : 'Reply to this email with each one as you have it.';
  out.push({ json: { partner_id: p.id, kind: 'reminder', to: p.email, subject: `Reminder ${c.n}: ${c.missing.length} item(s) still needed`, text:
`Hi ${p.name},

We are still waiting on:
${list}

${closing}

Cedar Lane Services` } });
  digest.push(`${p.name}: reminder ${c.n}, ${c.missing.length} missing (${c.missing.join(', ')})`);
}
if (digest.length) {
  out.push({ json: { partner_id: 0, kind: 'digest', to: $env.TEAM_EMAIL, subject: `Onboarding digest: ${digest.length} partner(s) chased`, text: digest.join('\n') } });
}
return out;
"""


def build() -> dict:
    nodes = [
        {"id": "w1", "name": "Partner signup", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0], "webhookId": "partner-signup",
         "parameters": {"httpMethod": "POST", "path": "partner-signup", "responseMode": "responseNode", "options": {}}},
        http("h1", "Register in ledger", "/ledger/register",
             "={{ JSON.stringify({ idempotency_key: $json.headers['idempotency-key'] || $json.body.signup_id, name: $json.body.name, email: $json.body.email, partner_type: $json.body.partner_type, tz: $json.body.tz, kickoff: $json.body.kickoff }) }}",
             [260, 0]),
        cond_if("i1", "Accepted by ledger?", "={{ $json.statusCode === 200 }}", [520, 0]),
        cond_if("i2", "New signup?", "={{ $json.body.created }}", [780, -120]),
        code("c1", "Compose welcome and team summary", COMPOSE_SIGNUP, [1040, -120]),
        email("e1", "Send signup emails", [1300, -120]),
        respond("r1", "Reply accepted", "={{ JSON.stringify({ status: 'accepted', partner_id: $('Register in ledger').first().json.body.partner.id }) }}", 202, [1560, -120]),
        respond("r2", "Reply duplicate", "={{ JSON.stringify({ status: 'duplicate', partner_id: $json.body.partner.id }) }}", 200, [1040, 40]),
        respond("r3", "Reply signup error", "={{ JSON.stringify({ error: $json.body.error || 'the ledger refused the signup' }) }}", "={{ $json.statusCode === 422 ? 422 : 502 }}", [780, 160]),

        {"id": "w2", "name": "Item received", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 400], "webhookId": "partner-item",
         "parameters": {"httpMethod": "POST", "path": "partner-item", "responseMode": "responseNode", "options": {}}},
        http("h2", "Mark item in ledger", "/ledger/item", "={{ JSON.stringify({ email: $json.body.email, item: $json.body.item }) }}", [260, 400]),
        cond_if("i3", "Item accepted?", "={{ $json.statusCode === 200 }}", [520, 400]),
        cond_if("i4", "Checklist now complete?", "={{ $json.body.just_completed }}", [780, 320]),
        code("c2", "Compose completion emails", COMPOSE_DONE, [1040, 280]),
        email("e2", "Send completion emails", [1300, 280]),
        respond("r4", "Reply complete", "={{ JSON.stringify({ status: 'complete', partner: $('Mark item in ledger').first().json.body.partner.email }) }}", 200, [1560, 280]),
        respond("r5", "Reply item recorded", "={{ JSON.stringify({ status: 'recorded', missing: $json.body.partner.missing }) }}", 200, [1040, 440]),
        respond("r6", "Reply item error", "={{ JSON.stringify({ error: $json.body.error || 'the ledger refused the item' }) }}", "={{ $json.statusCode === 404 || $json.statusCode === 422 ? $json.statusCode : 502 }}", [780, 560]),

        {"id": "s1", "name": "Chase schedule", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, 800],
         "parameters": {"rule": {"interval": [{"field": "seconds", "secondsInterval": CHASE_SECONDS}]}}},
        http("h3", "Claim reminders now due", "/ledger/chase", "={{ JSON.stringify({}) }}", [260, 800]),
        code("c3", "Compose reminders and alerts", COMPOSE_CHASE, [520, 800]),
        email("e3", "Send reminders", [780, 800], on_error=True),
        http("h4", "Release claim after a failed send", "/ledger/release", "={{ JSON.stringify({ partner_id: $json.partner_id, kind: $json.kind }) }}", [1040, 900]),
    ]
    conns = {
        "Partner signup": [["Register in ledger"]],
        "Register in ledger": [["Accepted by ledger?"]],
        "Accepted by ledger?": [["New signup?"], ["Reply signup error"]],
        "New signup?": [["Compose welcome and team summary"], ["Reply duplicate"]],
        "Compose welcome and team summary": [["Send signup emails"]],
        "Send signup emails": [["Reply accepted"]],
        "Item received": [["Mark item in ledger"]],
        "Mark item in ledger": [["Item accepted?"]],
        "Item accepted?": [["Checklist now complete?"], ["Reply item error"]],
        "Checklist now complete?": [["Compose completion emails"], ["Reply item recorded"]],
        "Compose completion emails": [["Send completion emails"]],
        "Send completion emails": [["Reply complete"]],
        "Chase schedule": [["Claim reminders now due"]],
        "Claim reminders now due": [["Compose reminders and alerts"]],
        "Compose reminders and alerts": [["Send reminders"]],
        "Send reminders": [[], ["Release claim after a failed send"]],
    }
    connections = {src: {"main": [[{"node": t, "type": "main", "index": 0} for t in branch] for branch in branches]} for src, branches in conns.items()}
    return {"id": "partner-onboarding", "name": "Partner onboarding", "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1"}, "pinData": {}}


if __name__ == "__main__":
    out = HERE / "seed" / "onboarding.workflow.json"
    out.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.name}: {len(build()['nodes'])} nodes", file=sys.stderr)
