import { useCallback, useEffect, useState } from "react";
import { Trouble } from "../lib/Detail";
import { api, useEventStream, type Frame } from "../lib/stream";
import "./evaluation.css";

/* What was measured, read back.
 *
 * These are recorded runs, not live ones. A full run is 240 model calls
 * against a daily quota, which is a decision someone makes deliberately at
 * a terminal -- not something a page fires on a click. What is useful
 * interactively is re-asking one question against the system as it stands
 * now, and seeing whether the answer has changed since the run.
 */

type RunRow = {
  id: string;
  kind: "questions" | "tools" | "systems";
  at: string | null;
  headline: any;
  unreadable?: boolean;
};

const ARM_NAMES: Record<string, string> = {
  full: "the system as built",
  vector_only: "similarity alone, no filters",
  no_memory: "the model with no memory",
};

export function Evaluation() {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [run, setRun] = useState<any>(null);

  useEffect(() => {
    api<{ runs: RunRow[] }>("/evaluation/runs")
      .then((body) => {
        setRuns(body.runs);
        const first = body.runs.find((r) => r.kind === "questions");
        if (first) setOpenId(first.id);
      })
      .catch((e) => {
        setRuns([]);
        setTrouble(String(e?.message ?? e));
      });
  }, []);

  useEffect(() => {
    if (!openId) return;
    setRun(null);
    api(`/evaluation/runs/${openId}`).then(setRun).catch(() => setRun(null));
  }, [openId]);

  return (
    <div className="evaluation">
      <header className="eval-head">
        <h1 className="display">
          what kivi gets <span className="swipe">right.</span>
        </h1>
        <p className="muted">
          recorded runs. the arms are the argument — if memory changed
          nothing, the numbers would match.
        </p>
      </header>

      {trouble && <Trouble error={trouble} />}

      {!trouble && runs.length === 0 && (
        <p className="muted idle-note">
          no runs recorded yet. run{" "}
          <span className="mono">
            docker compose exec backend python -m evaluation.run
          </span>{" "}
          and they will appear here.
        </p>
      )}

      <div className="eval-body">
        <aside className="run-list">
          {runs.map((row) => (
            <button
              key={row.id}
              className={`run ${openId === row.id ? "open" : ""}`}
              onClick={() => setOpenId(row.id)}
            >
              <span className="run-kind micro">{row.kind}</span>
              <span className="run-when">
                {row.at
                  ? new Date(row.at).toLocaleString(undefined, {
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })
                  : row.id}
              </span>
              <span className="run-score mono">{summarise(row)}</span>
            </button>
          ))}
        </aside>

        <section className="run-detail">
          {!run && openId && <p className="muted">opening…</p>}
          {run?.kind === "questions" && <Questions run={run} />}
          {run && run.kind !== "questions" && (
            <pre className="report">{run.report_md ?? "no write-up recorded."}</pre>
          )}
        </section>
      </div>
    </div>
  );
}

function summarise(row: RunRow): string {
  if (row.unreadable) return "unreadable";
  if (row.kind === "questions") {
    const full = row.headline?.arms?.full;
    return full ? `${full.correct}/${full.n}` : "—";
  }
  if (row.kind === "tools") return `${row.headline.correct}/${row.headline.total}`;
  return `${row.headline.events ?? "—"} takes`;
}

function Questions({ run }: { run: any }) {
  const overall = run.data.summary?.overall ?? {};
  const byCategory = run.data.summary?.by_category ?? {};
  const arms: string[] = run.data.arms ?? Object.keys(overall);

  return (
    <>
      <h2 className="micro heading">the three arms</h2>
      <div className="arms">
        {arms.map((arm) => {
          const score = overall[arm];
          if (!score) return null;
          return (
            <div className="arm" key={arm}>
              <div className="arm-label">
                <span>{ARM_NAMES[arm] ?? arm}</span>
                <span className="mono">
                  {score.correct}/{score.n}
                </span>
              </div>
              {/* Absolute scale. Scaling to the best arm made whichever
                  arm won fill the track, so 41/60 drew as a full bar. */}
              <div className="bar-track">
                <div
                  className={`bar ${arm === "no_memory" ? "dim" : ""}`}
                  style={{ width: `${Math.min(score.accuracy, 1) * 100}%` }}
                />
              </div>
              <span className="muted small">
                {Math.round(score.accuracy * 100)}% · p50{" "}
                {Math.round((score.latency_p50_ms ?? 0) / 100) / 10}s
              </span>
            </div>
          );
        })}
      </div>

      {Object.keys(byCategory).length > 0 && (
        <>
          <h2 className="micro heading">by kind of question</h2>
          <div className="rows">
            {Object.entries(byCategory).map(([name, scores]: any) => {
              const full = scores.full ?? scores;
              return (
                <div className="row cat" key={name}>
                  <span className="cat-name">{name.replace(/_/g, " ")}</span>
                  <span className="bar-track thin">
                    <span
                      className="bar"
                      style={{
                        width: `${((full.correct ?? 0) / (full.n || 1)) * 100}%`,
                      }}
                    />
                  </span>
                  <span className="mono">
                    {full.correct}/{full.n}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}

      <h2 className="micro heading">every question</h2>
      <div className="rows">
        {(run.data.questions ?? []).map((q: any) => (
          <Question key={q.id} q={q} />
        ))}
      </div>
    </>
  );
}

function Question({ q }: { q: any }) {
  const [open, setOpen] = useState(false);
  const full = q.arms?.full ?? {};

  return (
    <div className="row question" onClick={() => setOpen(!open)}>
      <span className={`verdict ${full.correct ? "right" : "wrong"}`}>
        {full.correct ? "✓" : "✗"}
      </span>
      <div className="q-main">
        <span className="q-text">{q.question}</span>
        {open && (
          <div className="q-detail" onClick={(e) => e.stopPropagation()}>
            {Object.entries(q.arms ?? {}).map(([arm, result]: any) => (
              <div className="q-arm" key={arm}>
                <span className="micro">{ARM_NAMES[arm] ?? arm}</span>
                <p className="q-answer">
                  {result.answered ? result.text : <em>declined to answer</em>}
                </p>
                {result.why && <p className="muted small">{result.why}</p>}
              </div>
            ))}
            <AskAgain question={q.question} now={q.now} recorded={full.text} />
          </div>
        )}
      </div>
      <span className="micro q-cat">{q.category?.replace(/_/g, " ")}</span>
    </div>
  );
}

/* One question, re-asked against the system as it is now. A few calls,
 * not 240 -- which is the version of "run the evaluation" that belongs
 * on a page. */
function AskAgain({
  question,
  now,
  recorded,
}: {
  question: string;
  now?: string;
  recorded?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [stage, setStage] = useState<string>("");

  const onFrame = useCallback((frame: Frame) => {
    if (frame.event === "step") setStage(frame.data.stage);
    if (frame.event === "answer") {
      const result = frame.data.steps?.find((s: any) => s.result?.answer)?.result;
      setAnswer(result?.answer ?? "no answer");
    }
  }, []);

  const state = useEventStream(url, onFrame, ["hello", "step", "answer", "done", "error"]);

  const run = () => {
    setAnswer(null);
    // The recorded run fixed the clock at the corpus date, so this one
    // must too -- "yesterday" resolved against today would be a different
    // question, and any difference in the answer would mean nothing.
    const params = new URLSearchParams({ request: question });
    if (now) params.set("now", now);
    // Without this the second click builds a byte-identical URL, React skips
    // the state update, the effect never re-runs -- and the answer that was
    // just cleared never comes back.
    params.set("_", String(Date.now()));
    setUrl(`/stream/ask?${params}`);
  };

  return (
    <div className="again">
      <button className="action" onClick={run} disabled={state === "open"}>
        {state === "open" ? `${stage}…` : "ask kivi again, now"}
      </button>
      {answer && (
        <div className="again-answer">
          <p>{answer}</p>
          {/* A real comparison now: same question, same reference date,
              the system as it stands today. A difference here is the
              system having changed, not the clock. */}
          {recorded && answer.trim() !== recorded.trim() && (
            <span className="micro changed-since">
              the answer has changed since this run
            </span>
          )}
        </div>
      )}
    </div>
  );
}
