# Running Kivi

Everything runs in Docker except the frontend dev server. Postgres holds
the data, one container serves the API, another drains the work queue.

---

## First run

```bash
cp .env.example .env          # then paste your Gemini API key into it
docker compose up -d
docker compose exec backend alembic upgrade head
```

The API is on **http://localhost:8000**, docs at `/docs`.

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

## The frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies the API, so the browser stays on one origin and
server-sent events are not a CORS problem.

For a single-container deployment:

```bash
npm run build        # writes into backend/app/static/
```

The API serves it at `/` once it exists. The build output is gitignored,
and the API skips the mount entirely when it is absent — so a fresh clone
runs headless without complaint.

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
