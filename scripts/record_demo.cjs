// Records the walkthrough of the running stack (docker compose up -d --build) as docs/recording/walkthrough.webm
// and writes the scene marks to docs/recording/marks.json. Captions are part of the page, so they are on the real frames.
//   node scripts/record_demo.cjs        (set PLAYWRIGHT_PATH if playwright is not installed next to the repo)
// Every action is followed by a wait for what it should have caused, never a fixed sleep longer than a reading pause.
'use strict';
const fs = require('fs');
const path = require('path');
let pw;
try { pw = require('playwright'); } catch (e) { pw = require(process.env.PLAYWRIGHT_PATH); }
const { chromium } = pw;

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'docs', 'recording');
const DESK = 'http://localhost:8080';
const MAIL = 'http://localhost:8025';
const READ = 2400; // a reading pause after a caption changes
const run = Math.random().toString(36).slice(2, 7);

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  for (const f of fs.readdirSync(OUT)) if (f.endsWith('.webm')) fs.unlinkSync(path.join(OUT, f));
  const browser = await chromium.launch({ headless: true, args: ['--mute-audio'] });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, recordVideo: { dir: OUT, size: { width: 1280, height: 800 } } });
  const page = await ctx.newPage();
  const REC0 = Date.now();
  const marks = [];
  const mark = (name) => marks.push({ name, t: +((Date.now() - REC0) / 1000).toFixed(2) });
  const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });
  const caption = async (text) => {
    await page.evaluate((t) => {
      let c = document.getElementById('cap');
      if (!c) {
        c = document.createElement('div'); c.id = 'cap';
        c.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;background:rgba(20,28,38,.94);color:#fff;font:600 24px/1.3 system-ui,sans-serif;padding:16px 28px;text-align:center;';
        document.body.appendChild(c);
      }
      c.textContent = t;
    }, text);
    await page.waitForTimeout(READ);
  };
  const until = (fn, arg, timeout = 60000) => page.waitForFunction(fn, arg, { polling: 250, timeout });

  const email = `riley.${run}@example.test`;
  await page.goto(DESK, { waitUntil: 'load' });
  await until(() => document.getElementById('clock').textContent.startsWith('Desk clock'));
  await page.fill('#s_email', email);
  await caption('A new tax partner signs up on the form.'); mark('signup');
  await page.click('#signupForm button[type=submit]');
  await until(() => document.getElementById('signupStatus').textContent.startsWith('Signed up'));
  await until((e) => [...document.querySelectorAll('#board tr')].some((r) => r.textContent.includes(e)), email);
  await caption('n8n runs the workflow: welcome email and team summary go out.'); await shot('1-signed-up'); mark('signed-up');
  await caption('The form is sent a second time. Nothing goes out twice.');
  await page.click('#signupForm button[type=submit]');
  await until(() => document.getElementById('signupStatus').textContent.startsWith('Already signed up'));
  await caption('The ledger saw the same signup and refused to repeat it.'); await shot('2-duplicate'); mark('duplicate');
  await caption('The team ticks items off as they arrive.');
  await page.click('#board .chip.missing >> text=Signed partner agreement');
  await until(() => document.querySelectorAll('#board .chip.done').length >= 1);
  await page.click('#board .chip.missing >> text=Profile photo');
  await until(() => document.querySelectorAll('#board .chip.done').length >= 2);
  await shot('3-items'); mark('items');
  await caption('Two days later the chase runs. n8n checks every 15 seconds.');
  await page.click('#skip2d'); mark('skip');
  await until(() => [...document.querySelectorAll('#board tr')].some((r) => r.lastElementChild && r.lastElementChild.textContent.trim() === '1'), null, 60000);
  await caption('Reminder 1 went out. It lists only the 3 items still missing.'); await shot('4-chased'); mark('chased');

  await page.goto(MAIL, { waitUntil: 'load' });
  await until((e) => document.body.innerText.includes('Reminder 1'), email);
  await caption('The mail catcher holds every email the system sent.'); await shot('5-inbox'); mark('inbox');
  await page.getByText('Reminder 1: 3 item').first().click();
  await until(() => document.body.innerText.includes('still waiting on') || document.querySelector('iframe'));
  await page.waitForTimeout(600);
  await caption('The reminder names what is missing and nothing else.'); await shot('6-reminder'); mark('reminder');

  await page.goto(DESK, { waitUntil: 'load' });
  await page.click('nav button[data-tab=chat]');
  await caption('A visitor asks the chat a question.'); mark('chat');
  await page.fill('#chatInput', 'How much does credit repair cost?');
  await page.click('#chatForm button[type=submit]');
  await until(() => document.querySelectorAll('#chatlog .msg.bot').length >= 1, null, 90000);
  await caption('It answers from the knowledge base and routes the lead.'); await shot('7-chat-answer'); mark('chat-answer');
  await page.fill('#chatInput', 'Where is my refund? It has been six weeks. My number is 415-555-0134.');
  await page.click('#chatForm button[type=submit]');
  await until(() => document.querySelectorAll('#chatlog .msg.bot').length >= 2 && document.querySelector('.handoff'), null, 90000);
  await caption('A personal question goes to a person. The phone number was hidden first.'); await shot('8-handoff'); mark('handoff');

  await page.click('nav button[data-tab=bookings]');
  await caption('A client books a consultation in their own time zone.');
  const when = new Date(Date.now() + 5 * 864e5); const p2 = (n) => String(n).padStart(2, '0');
  await page.fill('#b_time', `${when.getFullYear()}-${p2(when.getMonth() + 1)}-${p2(when.getDate())}T10:00`);
  await page.click('#bookForm button[type=submit]');
  await until(() => document.querySelectorAll('#bookings tr').length >= 1);
  await caption('Confirmation sent with a calendar invite. Reminders follow at 1 day and 2 hours.'); await shot('9-booked'); mark('booked');
  await page.click('#bookings button >> text=Move +1 day');
  await until(() => document.getElementById('outbox').textContent.includes('Moved'));
  await caption('Moved: a new invite goes out and the reminders follow the new time.'); mark('moved');
  await page.click('#bookings button >> text=Cancel');
  await until(() => document.getElementById('outbox').textContent.includes('Cancelled'));
  await caption('Cancelled: one notice, and no reminder will follow.'); await shot('10-cancelled'); mark('cancelled');

  await page.click('nav button[data-tab=partners]');
  await caption('Every email above went to a local mail catcher. None left this computer.'); mark('end');
  await page.waitForTimeout(READ);
  fs.writeFileSync(path.join(OUT, 'marks.json'), JSON.stringify({ email_suffix: run, marks }, null, 1));
  await ctx.close();
  await browser.close();
  const vids = fs.readdirSync(OUT).filter((f) => f.endsWith('.webm'));
  fs.renameSync(path.join(OUT, vids[0]), path.join(OUT, 'walkthrough.webm'));
  console.log('recorded', path.join(OUT, 'walkthrough.webm'), marks.map((m) => `${m.name}@${m.t}`).join(' '));
})().catch((e) => { console.error('RECORD FAILED:', e.message); process.exit(1); });
