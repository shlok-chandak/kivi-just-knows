# Running Kivi

## Primary review method

**A containerised application and database, run locally.** Everything —
Postgres, the API, and the background worker — comes up with
`docker compose up -d`. The interface is served by the API itself at
**http://localhost:8000**; no separate frontend process is needed to review
the product. Nothing is hosted and nothing needs deploying.

### What you need

| | |
|---|---|
| Docker Engine | 24 or newer (developed on 29.7) |
| Docker Compose | v2 or newer, as the `docker compose` subcommand (developed on v5.5) |
| a Gemini API key | free tier is enough — see `.env.example` |

Nothing else. Python 3.11 and Postgres 16 with `pgvector` are inside the
images, so no local install of either is required. Node 20+ is needed **only**
if you want to change the frontend; the built interface is served by the API.

---

## First run

```bash
cp .env.example .env          # then paste your Gemini API key into it
docker compose up -d
docker compose exec backend alembic upgrade head
```

`.env` needs exactly one value filled in: `LLM_API_KEY`. Every other variable
in `.env.example` has a working default and can be left blank.

The API and the interface are both on **http://localhost:8000**; API docs are
at `/docs`.

Postgres needs `pgvector` and `pg_trgm`; the migrations create both, so
`alembic upgrade head` is not optional.

### Load the corpus

```bash
docker compose exec backend python -m scripts.import_corpus \
  corpus/corpus.jsonl --truncate
```

The import itself takes under a second — it stores the dictations and
queues the work. The **worker** then does the slow part: grouping them
into episodes and reading each one. That is about 135 model calls and
takes 10–25 minutes at the free tier's rate limit.

Watch it land:

```bash
curl -N localhost:8000/stream/ingest
```

or open the **talk** screen, which shows the same thing with counters,
and the **upload corpus** screen, which does the whole thing from the browser.

`--truncate` deletes that user's events, episodes, memories, embeddings,
traces, jobs and refusal log. It is scoped to one user, so `--user-id`
loads a second corpus without destroying the first.

---

## What to try

The interface is at **http://localhost:8000**. After the corpus has been read,
these are the interactions worth doing, in this order.

**1. Ask something the corpus answers.** On **talk**, switch to **hey kivi**:

```
what is the Pro tier price now
```

You get ₹349, cited to a dictation from 11 August. Open the citation to see
the original dictation with its raw transcript. This price was decided four
times across two months; the earlier three are in history, marked replaced.

```
how has the Pro tier price changed over time
```

You get the whole chain in one answer — ₹499, then ₹299 in June, then ₹349
in August. Asking *"what was it before"* instead returns the current price:
the question names no time and no old value, so nothing marks it as a
question about history. That is a real limitation, not a trick phrasing, and
it is listed in the README's known limits.

**2. Watch a dictation become a belief.** Switch back to **dictation**, say a
few things (⌘↩ between each), then press **finish this episode now** rather
than waiting twenty minutes for the stretch to close on its own. The panel
underneath shows the takes gathering, the queue draining, and the beliefs that
came out.

**3. Ask something it should refuse.**

```
what is our office wifi password
```

Never said, so Kivi says it does not know rather than inventing one.

**4. See what it declined to keep.** The **not kept** screen lists everything
refused, with the rule that refused it. Sensitive dictations show no content,
because they were never stored — the screen can say a refusal happened but
cannot show you what it refused.

**5. See what it believes, and delete something.** The **memory** screen lists
every belief with its evidence. "history" shows replaced ones. Deleting is
immediate and permanent.

**6. Read the measurements.** The **evaluation** screen reads the recorded
runs in `evaluation/results/`.

---

## Importing another corpus

```bash
docker compose exec backend python -m scripts.import_corpus \
  /path/to/yours.jsonl --truncate
```

Mount the file somewhere the container can see it, or drop it in `corpus/`,
which is already mounted. Then wait for the worker to drain — the **talk**
screen's panel, or `curl -N localhost:8000/stream/ingest`, both show progress.

**The format.** JSON Lines, one dictation per line, or a single JSON array.

```json
{"occurred_at": "2026-06-15T09:12:00+05:30",
 "app": "slack",
 "raw_asr": "move the standup to ten",
 "formatted_text": "Move the standup to 10.",
 "committed_text": "Move the standup to 10."}
```

| field | |
|---|---|
| `occurred_at` | **required.** ISO 8601 **with a timezone** — a naive timestamp is rejected rather than assumed to be UTC. |
| `formatted_text` | the tidied text. This is what gets read. Required unless `raw_asr` is present. |
| `raw_asr` | optional. The untidied transcript. |
| `committed_text` | optional. What was actually sent after any edit. An empty string means it was discarded and nothing is remembered from it. |
| `app` | optional. Where it was said. Dictation in an editor is treated as an instruction to a tool, so no beliefs are drawn from it. |
| `external_id` | optional. Your id for the record. Re-importing the same file updates rather than duplicates. |
| `context_hash` | optional. An opaque id for the window or thread. Only ever compared for equality. |
| `asr_confidence`, `duration_ms` | optional. Used to spot a transcript the recogniser was guessing at. |

Unknown fields are ignored. Any other shape can be remapped without editing
the file:

```bash
docker compose exec backend python -m scripts.import_corpus yours.csv \
  --mapping '{"timestamp":"occurred_at","transcript":"raw_asr"}'
```

Add `--dry-run` to validate without writing, or `--strict` to stop on the
first bad record instead of skipping it.

---

## Changing the frontend

**Not needed to review the product** — the built interface is committed and
served by the API at http://localhost:8000. This section is only for editing
it.

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Node 20 or newer. The dev server proxies the API, so the browser stays on one
origin and server-sent events are not a CORS problem. Every router prefix the
API mounts must be listed in `vite.config.ts`; a missing one silently returns
the page's own HTML instead of JSON.

```bash
npm run build        # writes into backend/app/static/
```

The build output **is committed**, unusually and deliberately: compose
bind-mounts `./backend` over `/app`, so anything built inside the image is
shadowed by the host directory, and without these files a fresh clone would
serve no interface at all. Rebuild and commit after changing anything under
`frontend/src`.

---

## Tests

```bash
./run-tests.sh                      # everything
./run-tests.sh tests/test_claims.py # one file
```

Use the script rather than calling pytest directly. It stops the worker
first: the worker competes with the tests for queue rows, which makes
queue and pipeline assertions fail intermittently and for no visible
reason.

Tests run against a **separate database**, created once:

```bash
docker compose exec db psql -U kivi -d postgres \
  -c "CREATE DATABASE kivi_test OWNER kivi;"
docker compose run --rm \
  -e DATABASE_URL=postgresql+psycopg://kivi:kivi@db:5432/kivi_test \
  worker alembic upgrade head
```

Re-run the second command after adding a migration. `conftest.py` asserts
it is pointed at `kivi_test` and refuses to run otherwise — tests are
destructive by design, and one of them imports a fixture with
`--truncate`.

---

## Evaluation

```bash
docker compose run --rm --no-deps \
  -v "$PWD/evaluation:/app/evaluation" \
  worker python -m evaluation.run
```

**Not** `docker compose exec backend`. The `backend` service mounts
`evaluation/` read-only — deliberately, so the API can report
measurements but never edit them — and the harness needs to write its
results. A service's own mount wins over one added with `-v`, so the run
goes through `worker`, which has no mount to conflict with. Getting this
wrong produces a confusing `Read-only file system` error deep in the run.

Useful flags: `--arms full`, `--limit 10`, `--category superseded`,
`--rescore` (re-marks a recorded run without spending any calls).

Other harnesses, same invocation:

```
python -m evaluation.run_tools     # tool safety properties
python -m evaluation.systems       # latency, cost, storage, throughput
```

`systems.py` reads recorded traces rather than making calls, so it is
free to re-run.

Results land in `evaluation/results/<timestamp>/` and are gitignored.
The `evaluation` screen reads them back.

---

## Things that will waste your time

**The worker runs the code it imported at startup.** Editing a file it
uses changes nothing until you restart it. This has cost hours more than
once:

```bash
docker compose restart worker
```

**The free-tier quota resets at midnight Pacific, not UTC.** 500 requests
per day, per project, per model. A run that starts at 06:00 UTC is still
inside the previous Pacific day and may have almost nothing left. Check
what the provider thinks, not what your own traces say — `--truncate`
deletes traces, and the provider remembers what your database forgot.

**Per-job backoff does not save you from a daily quota.** Each job
correctly waits the delay the provider asks for, but with a hundred jobs
due there is always another one ready, so the aggregate request rate
stays pinned at the client maximum. Stop the worker until the reset
rather than letting it hammer.

**Nothing is lost when the quota runs out.** A rate-limited job is
rescheduled without consuming a retry attempt, so jobs cannot exhaust
themselves against a wall. Restart the worker after the reset and it
picks up exactly where it stopped.

**Costs are computed at list price, not billed.** Calls are free-tier, so
nothing is charged. A model missing from `app/llm/pricing.py` falls back
to the most expensive known rate and warns on every call — which
overstates rather than hides. After adding a rate, recompute what is
already stored:

```bash
docker compose exec backend python -m scripts.recost_traces --dry-run
docker compose exec backend python -m scripts.recost_traces
```

It recomputes from the token counts already on each step, so nothing is
re-measured and nothing is guessed.

---

## Resetting

```bash
docker compose down -v        # drops the database volume too
docker compose up -d
docker compose exec backend alembic upgrade head
```

To clear the data but keep the schema, re-run the importer with
`--truncate`.
