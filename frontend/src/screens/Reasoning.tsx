import { useCallback, useEffect, useState } from "react";
import { Json, Trouble } from "../lib/Detail";
import { api } from "../lib/stream";
import { Funnel } from "../lib/Funnel";
import "./reasoning.css";

/* Why it answered that, for anything it has ever answered.
 *
 * The talk screen shows a question narrowing as it runs, which is the right
 * thing while you are waiting and useless afterwards. This is the same
 * record read back: every question and every ingestion the system has
 * performed, with the stages it went through and what each one decided.
 *
 * The point of it is disagreement. A number in a report you cannot open is
 * a claim; a number you can open is evidence. So every step shows what it
 * was given, what it chose, and what it cost -- and a question can be run
 * again with its memory taken away, to see whether the memory was doing
 * the work or the model was guessing well.
 */

const KINDS = [
  { id: "query", label: "questions" },
  { id: "ingest", label: "reading dictations" },
  { id: "", label: "everything" },
];

type Row = {
  id: string;
  kind: string;
  input: string | null;
  final_output: string | null;
  outcome: string | null;
  latency_ms: number | null;
  cost_usd: number | null;
  tokens: { input: number; output: number } | null;
  at: string;
};

// The stages, in words a person can read. Anything not named here still
// shows, under its own name -- an unfamiliar stage is information too.
const STAGE_NAMES: Record<string, string> = {
  parse: "read the question",
  plan: "chose what to do",
  recall: "searched memory and answered",
  sql_filter: "narrowed by time and kind",
  vector_search: "searched by meaning",
  rank: "ordered what it found",
  junk_gate: "checked it was worth keeping",
  embed: "indexed for meaning-search",
  episode_assign: "filed into a stretch",
  episode_consolidate: "read the stretch",
  ignore_filter: "decided what to decline",
};

export function Reasoning() {
  const [kind, setKind] = useState("query");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [trouble, setTrouble] = useState<string | null>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: "40" });
    if (kind) params.set("kind", kind);
    const body = await api<{ traces: Row[] }>(`/traces?${params}`);
    setRows(body.traces);
    // The newest one opens itself. A list of collapsed rows makes you click
    // before the screen says anything, and the thing you almost always want
    // is the question you just asked.
    setOpen(body.traces[0]?.id ?? null);
  }, [kind]);

  useEffect(() => {
    setTrouble(null);
    load().catch((e) => {
      setRows([]);
      setTrouble(String(e?.message ?? e));
    });
  }, [load]);

  return (
    <div className="reasoning">
      <header className="reasoning-head">
        <h1 className="display">
          why it <span className="swipe">said that.</span>
        </h1>
        <p className="muted">
          every question and every dictation kivi has read, with the steps it
          took and what each one decided. nothing here is a log — these are
          the rows the answer was actually built from.
        </p>
      </header>

      <div className="apps">
        {KINDS.map((k) => (
          <button
            key={k.label}
            className="pill"
            aria-pressed={kind === k.id}
            onClick={() => {
              setKind(k.id);
              setOpen(null);
            }}
          >
            {k.label}
          </button>
        ))}
      </div>

      {trouble && (
        <Trouble
          error={trouble}
          retry={() => {
            setTrouble(null);
            load().catch((e) => setTrouble(String(e?.message ?? e)));
          }}
        />
      )}

      {!trouble && rows?.length === 0 && (
        <p className="muted idle-note">
          nothing yet. ask kivi something and it will appear here.
        </p>
      )}

      <div className="cards">
        {rows?.map((row) => (
          <article
            key={row.id}
            className={`card trace ${row.outcome ?? ""}`}
            onClick={() => setOpen(open === row.id ? null : row.id)}
          >
            <div className="trace-main">
              <h3 className="claim">
                {row.input || <em className="muted">a dictation being read</em>}
              </h3>
              <p className="trace-answer muted">
                {row.final_output || "—"}
              </p>
              <div className="beneath">
                <span className={`tag ${row.outcome}`}>{row.outcome}</span>
                <span className="muted small">
                  {row.latency_ms != null && `${(row.latency_ms / 1000).toFixed(1)}s`}
                  {row.cost_usd ? ` · $${row.cost_usd.toFixed(6)}` : ""}
                  {row.tokens?.input
                    ? ` · ${row.tokens.input + row.tokens.output} tokens`
                    : ""}
                </span>
                <span className="muted small when">
                  {new Date(row.at).toLocaleString(undefined, {
                    day: "numeric",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
            </div>
            <span className="chev">{open === row.id ? "↓" : "→"}</span>
            {open === row.id && <Steps id={row.id} kind={row.kind} />}
          </article>
        ))}
      </div>
    </div>
  );
}

function Steps({ id, kind }: { id: string; kind: string }) {
  const [detail, setDetail] = useState<any>(null);
  const [replay, setReplay] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/traces/${id}`).then(setDetail).catch(() => setDetail(null));
  }, [id]);

  // The counterfactual. Same question, same code, memory withheld -- the
  // only honest way to show that the memory is what produced the answer
  // rather than the model knowing it anyway.
  const runWithoutMemory = async () => {
    setBusy(true);
    try {
      setReplay(
        await api(`/traces/${id}/replay`, {
          method: "POST",
          body: JSON.stringify({ skip_filters: false, disable_stages: ["recall"] }),
        }),
      );
    } catch (problem: any) {
      setReplay({ error: problem.message ?? "could not replay that" });
    } finally {
      setBusy(false);
    }
  };

  if (!detail) return <p className="muted small detail">looking…</p>;

  return (
    <div className="detail timeline" onClick={(e) => e.stopPropagation()}>
      {detail.steps.map((step: any) => (
        <div className="stage" key={step.seq}>
          <span className="dot" />
          <div className="stage-body">
            <div className="stage-head">
              <span className="stage-name">
                {STAGE_NAMES[step.stage] ?? step.stage.replace(/_/g, " ")}
              </span>
              {step.model && (
                <span className="mono micro model">
                  {step.model}
                  {step.cost_usd ? ` · $${step.cost_usd.toFixed(6)}` : ""}
                  {step.latency_ms ? ` · ${step.latency_ms}ms` : ""}
                </span>
              )}
            </div>
            {step.decision && <p className="decided">{step.decision}</p>}
            {step.rationale && <p className="note">{step.rationale}</p>}
            {step.output?.funnel && (
              <Funnel
                funnel={step.output.funnel}
                shown={step.output.shown}
                notShown={step.output.not_shown}
              />
            )}
            {step.output?.filters_not_applied?.length > 0 && (
              <p className="note warn">
                asked for a filter it cannot apply:{" "}
                {step.output.filters_not_applied.join(", ")} — so the answer
                covers more than you asked for, and says so.
              </p>
            )}
            {(step.input || step.output) && (
              <Json
                value={{ given: step.input, decided: step.output }}
                label="exactly what this step saw"
              />
            )}
          </div>
        </div>
      ))}

      {kind === "query" && (
        <div className="counterfactual">
          <button className="pill" onClick={runWithoutMemory} disabled={busy}>
            {busy ? "running…" : "ask again with memory switched off"}
          </button>
          <span className="muted small">
            the same question, the same code, nothing remembered
          </span>
          {replay &&
            (replay.error ? (
              <div className="replayed">
                <p className="error">{replay.error}</p>
              </div>
            ) : (
              <div className="replayed">
                <div className="side">
                  <span className="micro">with memory</span>
                  <p>{replay.then?.answer ?? "it did not answer"}</p>
                </div>
                <div className="side off">
                  <span className="micro">without it</span>
                  <p>{replay.now?.answer ?? "it did not answer"}</p>
                </div>
                <p className="verdict small">
                  {replay.changed
                    ? "the answer changed — the memory is doing the work"
                    : "the answer did not change, which is worth knowing too"}
                </p>
                <Json value={replay} label="the whole replay" />
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

/* The narrowing, as a shape. Three numbers in a row are hard to compare;
   three bars are not. Scaled to the widest stage because a funnel is only
   ever read as a proportion of what came before it. */
