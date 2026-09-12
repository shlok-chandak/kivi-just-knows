# Kivi — semantic memory for a dictation app

**The problem.** Some people dictate all day — into Slack, Notion, WhatsApp,
their editor. Everything they say is transcribed and then thrown away. A
month later the decision they announced out loud is gone, even though the
software heard it.

**What this builds.** A memory layer that sits behind a dictation app. It
reads what you said, decides what is worth keeping, and answers questions
about it later — and can always show you why it answered that way, and what
it deliberately chose not to keep. You talk to it by saying **"Hey Kivi"**,
then asking for what you want in plain words.

**Three words used throughout:**

| | |
|---|---|
| **dictation** | one thing you said, once — the unit that arrives |
| **episode** | a stretch of dictations close together in time, read as a group |
| **belief** | something Kivi concluded you think is true, drawn from an episode |

The flow is always `dictation → episode → belief`, and §3 follows one all the
way through.

To run it, see **[RUN.md](RUN.md)**. This was built as a take-home
assignment; §8 maps the questions it was set to the places they are answered.

*On AI use: this codebase was written with Claude Code. Every design decision, the
schema, the retrieval strategy, what to measure, and what to leave unfixed,
was made and reviewed by me.*

---

## Contents

| | |
|---|---|
| [1. What you can ask it, and how well it does](#1-what-you-can-ask-it-and-how-well-it-does) | the scores, good and bad, up front |
| [2. The shape of the thing](#2-the-shape-of-the-thing) | the four moving parts |
| [3. What happens to one dictation](#3-what-happens-to-one-dictation) | from spoken words to a belief, and everything thrown away on the way |
| [4. How a question gets answered](#4-how-a-question-gets-answered) | the five steps from question to answer |
| [5. Why it is built this way](#5-why-it-is-built-this-way) | the decisions that were not obvious |
| [6. The corpus it was tested on](#6-the-corpus-it-was-tested-on) | how the test data was made, and why it isn't rigged |
| [7. What the evaluation actually showed](#7-what-the-evaluation-actually-showed) | every measured number, including the bad ones |
| [8. The six questions this was set](#8-the-six-questions-this-was-set-and-where-each-is-answered) | the assignment, mapped to answers |
| [9. Known limits](#9-known-limits) | what is broken, and why it was left |
| [10. Future scope](#10-future-scope) | what I would build next |

**In a hurry?** §1 is the honest summary and §7 is the evidence for it.

---

## 1. What you can ask it, and how well it does

You say "Hey Kivi" and then ask for something. There are five things it can
do: **answer a question**, **find a dictation**, **rewrite some text**,
**draft a message**, and **forget something**. Answering is the hard one, so
below it is broken down by the kind of question asked.

The scores come from a test set of 83 cases — 60 questions with known answers,
plus 23 checks on the other four abilities. Each number is how many of those
cases came out right. §7 explains how the test set was built and how much to
trust it; the weak spots are here at the top because they should be the first
thing you see, not the last.

### Good at it

| | |
|---|---|
| **Knowing when to say nothing** — refusing what it was never told, and what it was told in confidence | **18/18** |
| **Finding a dictation** — "the thing I said in Slack around 5" | 4/4 |
| **Rewriting text** without losing a price or a date | 4/4 |
| **Drafting a message** from what it knows, or withdrawing if it doesn't | 4/4 |
| **Forgetting on request** — deleting a belief, and refusing what it can't find | 5/5 |

### Decent at it

| | |
|---|---|
| **Answering a plain factual question** — "what time is standup" | 8/10 |
| **Summarising a whole thread** — "what happened with pricing" | 8/10 |
| **Keeping up when a decision changes** — answering ₹349, not ₹499 | 4/6 |
| **Questions about a person** — "what did Aditya say about the price" | 6/8 |

### Not good at it yet

| | |
|---|---|
| **Time-scoped questions** — "what did I say last week" | 4/8 |
| **Spotting private content in unfamiliar vocabulary** | **10/20** |
| **Telling two people with the same name apart** | — |

The examples above (a price that changed, a colleague called Aditya) come from
a synthetic corpus of 509 dictations written for testing — an invented founder
at an invented company. §6 explains how it was built and why it isn't rigged.

Of the weak spots, time questions are mostly fixed since that run. The
privacy one matters most and is not fixed; §10 says what would.

---

## 2. The shape of the thing

```
  dictations ──▶  ingest  ──▶  episodes  ──▶  beliefs
                    │             │              │
                 (filter)    (a stretch of    (what Kivi
                              activity)        thinks is true)
                                                   │
  your question ──▶ retrieve ──▶ answer ◀──────────┘
                                   │
                                 trace  (every step, recorded)
```

Four pieces, all in Docker:

| piece | what it does |
|---|---|
| **db** | PostgreSQL, with `pgvector` for meaning-search and `pg_trgm` for spelling-search |
| **backend** | The API, and the web UI |
| **worker** | Does the slow thinking in the background, so nothing blocks |
| the queue | Also PostgreSQL — jobs are rows in a table |

There is no Redis, no Celery, no separate vector database. One database holds
the text, the numbers, the embeddings and the job queue. This is a
single-user system on a laptop; a second piece of infrastructure would be a
second thing that can break.

**Models.** A small, cheap LLM (Gemini 3.1 Flash-Lite) does the high-volume
work. Embeddings are computed **locally** on the CPU, so searching your memory
never leaves the machine and never costs anything.

---

## 3. What happens to one dictation

Not much of what people say is worth remembering, so most of this is about
what gets thrown away, and where.

```
509 dictations
    │
    ├── 25  refused at the door ──────── never written down at all
    │
   484 stored
    │
    ├── 33  stored but ignored ───────── kept for the audit, never read
    │        · 8  the recogniser was guessing
    │        · 8  nothing actually said
    │        · 8  you deleted it instead of sending
    │        · 5  a retry of the previous line
    │        · 4  the timing didn't match the audio
    │
   451 read
    │
    └──▶ 146 episodes ──▶ 196 beliefs  (47 of them since replaced)
```

**Refused at the door.** If a dictation looks like a password, a medical
detail, or who you voted for, it is rejected *before* it is written to disk.
There is no copy anywhere, which means the UI can tell you a refusal happened
but can never show you what was refused. Storing it in order to display it
later would defeat the entire point.

**Stored but ignored.** These are kept, marked with a reason, and never read
again. They exist so you can ask "why didn't you remember that?" and get a
real answer.

**Episodes.** Dictations are grouped into stretches of activity purely by
time — an episode closes after 20 minutes of silence, or 2 hours, or 60
dictations. Never by topic. Grouping by similarity would mean the same corpus
imported twice produces different episodes, and then nothing is reproducible.

**Beliefs.** Each closed episode is read once, and proposes things worth
keeping. Most proposals are still rejected:

- **38** were instructions to a tool, not statements ("refactor the retry
  handler" is a thing you told your editor to do, not a fact about the world)
- **27** were sensitive on a second look
- **5** had nothing extractable

What survives becomes a belief, of one of four kinds: **fact**, **decision**,
**commitment**, **preference**.

**When a new belief contradicts an old one**, the old one is not deleted. It
is marked replaced, and linked to the thing that replaced it — so the price
chain reads end to end:

> ₹499 → ₹299 → ₹299 from October → **₹349** *(current)*

History stays reachable, because "what was the price before we raised it" is
a real question. It just never wins over the present.

---

## 4. How a question gets answered

```
  "what is the Pro tier price now"
        │
   1. read it        what kind of question is this? any dates in it?
        │
   2. filter         narrow by time, kind, status — in SQL
        │
   3. search         meaning-search + spelling-search, inside what's left
        │
   4. rank           relevance, then confidence, then freshness
        │
   5. answer         quoting what it relied on — or say it doesn't know
```

**Narrow first, search second.** "What did I say last Tuesday" is a date range
with a search inside it. Searching everything and then throwing away the wrong
dates wastes the search, and gets worse as memory grows.

**Two searches, not one.** Meaning-search is good at "how should I write to
this client" and bad at unusual proper nouns, because a name it has never seen
looks like noise. Spelling-search (trigrams) catches exactly those. Both run;
the results merge.

**Old is not wrong.** A preference from March is still your preference. So age
gently lowers a result's rank but never disqualifies it. Being *replaced* is
different from being *old*, and only the first one is disqualifying.

**Saying "I don't know" is a feature.** If nothing good comes back, Kivi says
so. A memory system that invents an answer is worse than no memory system,
because you stop being able to trust the ones it gets right.

**Every step is recorded.** Not logged — recorded, as rows, with the number of
candidates at each stage, which model was called, what it cost. You can replay
a question with a piece of the system switched off and see what changes.

---

## 5. Why it is built this way

The decisions that were not obvious:

**Two numbers, not one.** How *sure* Kivi is (built from how often you said
it) is kept separate from how *current* it is (which decays with time). One
confidence score would mean a fact you repeated ten times last year and one
you mentioned once this morning are indistinguishable, and they are not.

**Time boundaries, never semantic ones.** See episodes above. Reproducibility
beat cleverness.

**The small model everywhere.** The expensive model is configured but unused.
Query parsing is the most-called step and the least demanding — spending
large-model money on it is the easiest way to make a system cost ten times
what it should for no measurable gain.

**Refusal beats redaction.** Sensitive content is never stored, rather than
stored-and-hidden. Hidden things get unhidden by the next bug.

**Gemini rather than Sarvam's own models — a preference, not a capability
gap.** Sarvam was checked and can do this job: `sarvam-105b` supports strict
JSON-schema structured output, which is the one hard requirement here. Gemini
was chosen for a free tier generous enough to rebuild the whole corpus
repeatedly while tuning. The provider sits behind a single adapter
(`app/llm/client.py`), so switching is a small change rather than a rewrite.

**One transaction per job.** A job that is half-done is never visible to
anything else. If the worker dies mid-thought, the work simply hasn't
happened yet.

---

## 6. The corpus it was tested on

There is no public dataset of one person's dictations, so the corpus was
built — which raises the fair question of whether it was built to be easy.
Here is how that was avoided.

**The answers were written before the content.** `corpus/spec.yaml` was
authored by hand first: the cast, the projects, six decisions that get
revised, what must be refused, what must be ignored. The generator then
realises that spec into dictations. Because the structure came first, ground
truth *falls out* of it rather than being read back off the output — nobody
looked at what the system produced and then decided what the right answer
was.

**It regenerates byte for byte.** Seeded once, at `20260615`. Re-running the
generator right now reproduces `corpus.jsonl` with an identical SHA-256. A
corpus that drifts between runs would make every number measured against it
meaningless.

**Real dictation is short and elliptical, and this is too.**

| | |
|---|---|
| records | 509 over 66 days, 7 apps |
| words per dictation | 5 (p10) · **8 (median)** · 23 (p90) · 37 (max) |
| raw transcript differs from the tidied text | 499 / 509 |
| the user edited before sending | 202 / 366 |

Nobody dictates in full sentences. They name a person the first time and use
"he" or nothing afterwards — so a claim's subject is often only recoverable
from a dictation three earlier. **That is deliberately the hard part**: it
means string matching cannot find these claims and episode context has to.

**The recogniser errors are modelled on real failure modes**, not invented
ones: numbers arrive as words ("two ninety nine" for ₹299), unfamiliar Indian
names come out wrong ("additya", "pranam"), disfluencies survive, words
repeat, homophones slip through, and nothing is capitalised or punctuated.

**The user's edits only ever change punctuation, casing, or a fixed token —
never a word.** That is a deliberate constraint: it is what separates a style
signal from random noise.

**What ground truth asserts:** 6 decision arcs with their full history
(₹499 → ₹299 → **₹349**), 5 stated preferences, 10 style preferences, 1
deliberately ambiguous person (two people named Priya), and per-record labels
on 250 records.

**And then a second corpus, to check none of that was fitted.** A freight and
warehousing business in Chennai — different industry, different vocabulary,
written before the rules were tuned and not read since. It is stored under a
separate user id, so it cannot reach the main corpus even by accident. Its
results are reported in full below, including the one that is bad.

---

## 7. What the evaluation actually showed

60 questions with known answers, across seven kinds. All numbers below are
measured, including the ones that are bad.

### Does the memory actually help?

The same 60 questions were asked three times over, with a piece of the system
removed each time. If memory is doing the work, taking it away should hurt.

| what was switched on | score | |
|---|---|---|
| **everything** | **48/60 (80%)** | the real system |
| memory, but no structured filtering | 48/60 (80%) | search by meaning only |
| no memory at all | 19/60 (32%) | the language model answering alone |

**Memory is worth 29 questions.** And the 19 the model alone got right are
almost exactly the 18 where the right answer is *"I don't know"* — it scored
**zero facts**. Everything Kivi actually knows comes from the memory, not
from the model that phrases the answer.

**But structured filtering earned nothing.** Identical scores with it and
without. This is the most uncomfortable finding here and it is reported
rather than buried — on *this* corpus, at *this* size, searching by meaning
alone was enough. It should pay off as memory grows, but that is a
prediction, not a measurement.

### Why 80% here is not 100% elsewhere

A real language model answers every question in this test. Swap it for a stub
that just ranks stored text by word overlap and prints the winner, and a
score near 100% is easy — the questions and the stub share a vocabulary, so
they agree by construction. That measures the test, not the system.

Two things show this test measures something. The same questions and the same
marking give the no-memory run **32%**, so the marking can produce a low
score. And the questions were written from the corpus design before any
retrieval code existed, so nothing was tuned to pass them.

### Where it fails

| kind of question | score |
|---|---|
| unanswerable (should refuse) | 10/10 |
| sensitive (should refuse) | 8/8 |
| single fact | 8/10 |
| synthesis | 8/10 |
| entity | 6/8 |
| **superseded** | **4/6** |
| **temporal** | **4/8** |

The first two rows are the questions Kivi should refuse — ones the corpus
never answers, and ones about private things it was never supposed to keep.
Getting those right is the strongest behaviour: **18 out of 18**, and it
never invented an answer. It did wrongly refuse one question it could have
answered.

Time is the weakest, and this number is stale against Kivi's favour: three of the four temporal failures were "no window resolved",
caused by a date-parsing bug that has since been fixed. Re-running that
category alone after the fix scored **6/8**. The 48/60 headline above is
from the last *full* run, which predates the fix, so it understates the
current system — but a projected number is not a measured one, and only
measured numbers are quoted here.

The two failures that remain are a genuine disagreement about what "last
week" means: Kivi reads it as a rolling 7 days, the test expects the
calendar week. That is a decision nobody has made yet, not a bug.

### The other four things Kivi does

Answering questions is one of five capabilities. All five were measured:

| capability | score | what was checked |
|---|---|---|
| **answering** | **48/60** | the table above |
| picking the right tool | 5/6 | did the request go to the right place |
| finding a dictation | 4/4 | "the thing I said in Slack around 5" |
| rewriting text | 4/4 | every price and date survived the rewrite |
| drafting a message | 4/4 | cited when grounded, withdrew when it had nothing |
| editing memory | 5/5 | deleted correctly, refused what it couldn't find |

The four tools score **22/23** together. They are graded on things that have a
right answer — did the number survive, is every line cited, does deletion
refuse a topic it cannot find — rather than on whether the prose reads
nicely, which would need another model to judge and would measure that
model's taste.

Deletion was tested against the real corpus inside a transaction that was
rolled back, so it was proved destructive in the right way without destroying
anything.

### How steady these numbers are

The step that reads your question is itself a model call, so it is not
perfectly repeatable. "Forget everything about the nav redesign" routed to
*edit memory* on one run and *answer a question* on the next — same words,
same code. Across four tool runs, routing scored 6/6 three times and 5/6
once.

So some part of every score here is noise. Treat them as approximate: real in
direction, not trustworthy to the last point. Where a number did move for a
reason, it is called out — finding a dictation went 3/4 → 4/4 after a real
bug was fixed, and that one was not luck.

### On a corpus it had never seen

The second corpus from §6 — a freight and warehousing business, written
before any of the rules were tuned and never looked at since:

| | score | |
|---|---|---|
| recalled the right thing | 6/8 | |
| junk correctly ignored | 14/16 | |
| **sensitive correctly refused** | **10/20** | ⚠️ |
| keepable records wrongly refused | 0/50 | nothing good was lost |

**The sensitive filter caught half.** In-sample it looked excellent; on
unfamiliar vocabulary it missed 10 of 20. This is the honest limit of a
word-list approach and the single biggest weakness in the system. It is
reported because a safety number measured only on the data you designed
around is not really measured at all.

### What it costs

| | |
|---|---|
| model calls per dictation | 0.28 |
| cost per dictation | $0.0002 |
| finding the right memories | **41 ms** (typical) |
| a full question, end to end | 4.9 s (typical) |
| storage per dictation | 17 KB |

Retrieval is ~1% of the time a question takes. **Everything else is waiting
for the LLM.** Optimising the search would be optimising the wrong thing.

### One more measurement

The same corpus was rebuilt end-to-end on a newer model (3.5 Flash-Lite) to
check the choice was right. It scored **41/60 against 3.1's 48/60** and cost
1.4× more — it refused to answer five questions it could have answered. The
build stayed on 3.1. The comparison is kept in `evaluation/results/`.

---

## 8. The six questions this was set, and where each is answered

The assignment asked six things about how a memory system should work. This
table is the index to the answers.

| the question | where it is answered |
|---|---|
| What it learns, and what it deliberately ignores | §3 · and the **not kept** screen in the app, which lists every declined item with its reason |
| How a memory is created, stored, changed, removed | §3 · the replacement chain · and deletion from the **memory** screen |
| How facts, episodes and preferences relate | §3 — beliefs carry a kind (fact, decision, commitment, preference), and each is tied to the episode it came from |
| How understanding is retrieved | §4 · filter first, then search |
| How memory changes visible behaviour | §7 — with memory 80%, without it 32%, same questions |
| How an engineer inspects a result | the **why it said that** screen — every question and ingestion, opened to its steps, with the candidate funnel and a button to re-run it with memory switched off |

---

## 9. Known limits

Things found, understood, and left — each with the reason.

**Extraction sometimes drops the subject.** "The speaker is switching to
Cashfree" is a true belief that cannot be found by asking "what payment
gateway did we go with", because the words *gateway* and *payment* are not in
it. Diagnosed by replaying the question with parts of the system switched
off. The real fix is at write time — extraction must keep the subject that
makes a claim findable — so it was not patched at read time, which would have
meant tuning against one question we had already looked at.

**A question about the past has to say so.** History is stored and
retrievable — *"how has the Pro tier price changed over time"* answers with
the whole chain, ₹499 then ₹299 then ₹349. But *"what was the price before"*
returns the current price: the question carries no date and no old value, so
nothing in it marks the request as being about history rather than about now.
The fix is in how a question is read, not in what is stored.

**Kivi does not know who you were talking to.** Reading the recipient needs
OS accessibility APIs and per-app window parsers that break on every update,
and a wrong value would silently return the wrong person's conversation
instead of failing visibly. Time and app are reliable, so those are used.
People are still found by name in the text of what you said.

**The privacy filter is a list of words.** It caught 10 of 20 on the second
corpus, as reported above.
Real coverage needs a classifier.

**Style learning is specified but not built.** Kivi records that you edited a
draft, and what you changed, but does not learn from it yet.

**Contradiction detection was measured and abandoned.** Unrelated beliefs
scored *higher* similarity (0.24) than genuinely contradictory ones (0.15).
It needs entity resolution, not a better threshold — and tuning the threshold
would have been fitting to noise.

---

## 10. Future scope

In the order I would build them. Each follows from something measured above.

**1. Learn how you write, from the edits you make.** All three versions of
every dictation are already stored, so the edit is already recorded — Kivi
just doesn't learn from it yet. Edits that only touch punctuation or casing
are style, and repeated often enough they become a preference. That improves
dictations and anything Hey Kivi drafts, in one go.

**2. Stop episodes and beliefs repeating each other.** A summary and the
beliefs drawn from it often say the same thing, so both come back and crowd
out everything else. Fix it at read time — episodes stay bounded by time,
because that is what keeps two imports identical. A replaced belief's
summary should also carry the *replaced* tag, applied when read.

**3. Replace the privacy word list with a real model.** 10 of 20 is
the weakest number here, and a word list cannot be patched into competence. A
small PII model that runs locally is the right shape — Perplexity's recent
~600M one is the first to evaluate. Scoped but not attempted: there was no
time to measure it honestly, and an unmeasured safety component is worse than
a measured bad one.

**4. Keep the subject at extraction.** "The speaker is switching to Cashfree"
is true and unfindable. The fix is at write time, checked against the second
corpus
— fixing it at read time would be tuning against a failure already seen.

**5. Entity resolution.** Three problems are really one: contradiction
detection needs it, the two people named Priya need it, and person-scoped
questions need it. One fix, three unlocks.

**6. Report a range, not one number.** Routing scored 6/6 three times and 5/6
once on identical input. Running the suite several times would make every
figure in §7 honest about its own noise.
