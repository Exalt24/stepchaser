# Team guide: running the desk

Written for a team member who does not write code. The business in the examples (Cedar Lane Services) is a made-up sample. The screens and steps are the real ones.

## What it does for you

| Job | What happens without you | What you do |
| --- | --- | --- |
| A new tax partner signs up | A welcome email with the five checklist items goes to them and a summary goes to the team. | Nothing. |
| A partner has not sent everything | After 2 days they get a reminder listing only what is missing, up to 3 times, 2 days apart. | Click an item on the board when it arrives. |
| A partner goes quiet after 3 reminders | The team gets one "stalled" email and the reminders stop. | Phone or write to them yourself. |
| A client books a consultation | A confirmation with a calendar invite goes out, then reminders 1 day and 2 hours before. | Nothing. |
| A client moves or cancels a booking | A new invite or a cancellation notice goes out and the reminders follow the change. | Use the Move or Cancel button. |
| A website visitor asks a question | The chat answers from the knowledge base and says which service fits. | Read the handoff email if one arrives. |

## Start and stop

1. Start everything with `docker compose up -d --build` in the project folder. It takes about a minute the first time.
2. Open the desk page at http://localhost:8080 and the mail catcher at http://localhost:8025. The mail catcher shows every email the system sends. None leave the computer.
3. Stop with `docker compose down`. The partner and booking records are kept in the `runtime` folder.

## Every day (two minutes)

1. Open the desk page, tab "Partner onboarding". Read the line at the top of the board: partners, complete, waiting, stalled.
2. For every orange item you have received, click it. It turns green. Clicking twice does nothing.
3. Open the mail catcher. A "Stalled onboarding" email means a person should phone that partner today.
4. Open the "Lead chat" tab only if a "Handoff" email arrived. The email has a short summary, the visitor's email address and the conversation with identity and account numbers already hidden.

## When something looks wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| The partner says no welcome email came | The signup may have been refused or typed twice. | Search the board for their email. If they are there, the email went out once and is in the mail catcher. If not, submit the signup again. |
| A reminder did not go out | The mail server was down at that moment. The system puts the reminder back and tries again on its next check. | Wait 15 seconds. Nothing is lost. If it still does not go, open http://localhost:8080 and check the page loads, then run `docker compose ps` and start anything that is stopped. |
| A client booked a time that the system refuses | The time does not exist or happens twice on the night the clocks change. | Offer the client a different time. |
| The chat handed a simple question to a person | The system was not sure, or the answer was not in the knowledge base. | Answer the visitor yourself, then add the answer to `kb/faq.md` so the chat can answer next time. |
| A visitor typed an ID or account number | It was hidden before anything was stored. | Tell them not to send it that way and send the secure upload link. |
| The desk page says the workflow engine is not reachable | n8n is stopped or still starting. | Wait a minute, then run `docker compose up -d`. |

## Changing things

- **The wording of an email.** Open n8n at http://localhost:5678, open the workflow "Partner onboarding" and open one of the three nodes that start with "Compose". Change the text between the backticks. Save, then publish.
- **How often it checks.** Open the "Chase schedule" node. The sample checks every 15 seconds so you can watch it work. A real team sets it to once an hour. How long it waits between reminders is set by `REMIND_EVERY_SECONDS`, 2 days by default.
- **The checklist items.** They are listed in `stepchaser/ledger.py` under `REQUIRED_ITEMS`. Ask the developer to change them and to update the welcome email at the same time.

## Rules that never change

1. Nobody types or pastes a social security number, a tax ID, a bank account or a card number into the chat, an email or an assistant.
2. Questions about one person's file, refund or account are answered by a team member, never by the chat or the assistant.
3. Nothing is promised in a message: no refund, approval, score change or deadline the team has not set.
