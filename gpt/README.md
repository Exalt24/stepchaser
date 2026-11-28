# Custom GPT package: Cedar Lane Team Assistant

An assistant for a team: it answers from the written procedures and drafts client and partner messages in the house style. The business is a made-up sample. The package is the set of files ChatGPT's GPT builder takes, plus an evaluation, so it can be scored before anyone uploads it.

| File | What it is | Builder field |
| --- | --- | --- |
| `config.json` | Name, description, the file list | Name, Description |
| `instructions.md` | The behaviour and the rules it never breaks (2,181 of the 8,000 characters the box takes) | Instructions |
| `starters.json` | Four conversation starters | Conversation starters |
| `knowledge/*.md` and `../kb/*.md` | Six files: the onboarding procedure, appointment rules, house style, the sensitive information policy, the services and the common questions | Knowledge |
| `eval/cases.jsonl` | 40 cases with deterministic checks | Not uploaded |

## Check it and score it

```
python scripts/run_gpt_eval.py --validate-only    # limits only, no key needed
python scripts/run_gpt_eval.py                    # scores all 40 cases; needs LLM_API_KEY or GROQ_API_KEY
```

`gpt.py` loads the same files, refuses a package outside the builder's limits (instructions over 8,000 characters, more than 4 starters, more than 20 files, a missing file) and runs the instructions and files as a system prompt against any OpenAI-compatible endpoint. Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` to score it on another model. The latest scores are in [docs/proof/gpt_eval.txt](../docs/proof/gpt_eval.txt).

## Load it into ChatGPT

1. In ChatGPT open Explore GPTs, then Create, then the Configure tab.
2. Paste `config.json`'s name and description. Paste the whole of `instructions.md` into Instructions.
3. Add the four lines of `starters.json` as conversation starters.
4. Upload the six knowledge files listed in `config.json`.
5. Turn off web browsing, code interpreter and image generation, as `config.json` lists.
6. Try each starter and the cases in `eval/cases.jsonl` by hand. ChatGPT retrieves from the uploaded files instead of reading them whole, so its answers can differ from the scores here.
7. Keep it private or share it with the team only. The knowledge files describe a sample business and should be replaced with the real procedures first.

## Run it as a ChatGPT Project (free plan)

OpenAI no longer lets a personal account create or publish a custom GPT (its help page on GPT retirement points to Projects instead), so the same package was loaded into a ChatGPT Project on a free account on 2026-11-28. The Instructions field holds `instructions.md` plus the text of `../kb/faq.md`, because a free Project holds five files; the other five knowledge files were uploaded as sources.

Two of the four starters were asked in the Project and answered from the uploaded files:

- "What do we send a new tax partner, and when do we follow up?" gave the day-one welcome email, the five items, a reminder every 2 days, at most 3 reminders and then a stalled-partner alert ([screenshot](../docs/test-runs/chatgpt-project-onboarding-answer.png)).
- "A client pasted their account details in a message. What do I do?" gave the delete, do-not-repeat and secure-upload steps and a draft message with a placeholder link, and said a team member handles the client's own file ([screenshot](../docs/test-runs/chatgpt-project-sensitive-info-answer.png)).

No custom GPT was published from this repository; the Project is private.
