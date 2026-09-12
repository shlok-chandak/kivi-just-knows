import { useCallback, useEffect, useRef, useState } from "react";
import { Json, Step } from "../lib/Detail";
import { Funnel } from "../lib/Funnel";
import { api, useEventStream, type Frame } from "../lib/stream";
import "./talk.css";

/* The two things a person can say, in one box.
 *
 * A dictation goes in and a question comes out, and they are opposite
 * halves of the same system -- one writes memory, the other reads it. The
 * toggle is the honest way to show that: the same box, the same design,
 * and a flow underneath that says which way the data went.
 *
 * Neither half hides what it did. A dictation is followed until it lands
 * in a belief or is declined; a question shows what was searched and what
 * the answer rests on. Both open to the JSON underneath.
 */

const APPS = ["slack", "gmail", "notion", "cursor", "whatsapp", "linear"];
const ASK_FRAMES = ["hello", "step", "answer", "error", "done"];

type Mode = "dictate" | "ask";

export function Talk() {
  const [mode, setMode] = useState<Mode>("dictate");

  return (
    <div className="talk">
      <header className="talk-head">
        <h1 className="display">
          {mode === "dictate" ? (
            <>
              say it once. <span className="swipe">kivi's listening.</span>
            </>
          ) : (
            <>
              ask what <span className="swipe">kivi knows.</span>
            </>
          )}
        </h1>
        <p className="muted">
          {mode === "dictate"
            ? "a dictation is stored, grouped, and read for anything worth keeping."
            : "a question is answered only from what you actually said."}
        </p>
      </header>

      <div className="mode-row">
        <div className="toggle">
          <button
            className="pill"
            aria-pressed={mode === "dictate"}
            onClick={() => setMode("dictate")}
          >
            dictation
          </button>
          <button
            className="pill"
            aria-pressed={mode === "ask"}
            onClick={() => setMode("ask")}
          >
            hey kivi
          </button>
        </div>
        <span className="micro direction">
          {mode === "dictate" ? "writes to memory \u2192" : "\u2190 reads from memory"}
        </span>
      </div>

      {mode === "dictate" ? <Dictation /> : <Asking />}
    </div>
  );
}

/* --- dictating ----------------------------------------------------------- */

function Dictation() {
  const [text, setText] = useState("");
  const [app, setApp] = useState("slack");
  // Bumped whenever something lands, so the panel refreshes at once rather
  // than up to a poll late -- the gap reads as the click not working.
  const [pulse, setPulse] = useState(0);

  const [sending, setSending] = useState(false);
  const [refused, setRefused] = useState<any>(null);
  const [eventId, setEventId] = useState<string | null>(null);
  const [journey, setJourney] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  // Polled rather than streamed: the interesting part is an episode
  // waiting to close, which is a clock ticking rather than an event
  // arriving, and there is nothing for a stream to push during it.
  useEffect(() => {
    if (!eventId) return;
    let alive = true;
    const tick = async () => {
      try {
        const body = await api(`/events/${eventId}/journey`);
        if (alive) setJourney(body);
      } catch {
        /* the row may not be visible for a moment after the insert */
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [eventId]);

  const send = async () => {
    if (!text.trim()) return;
    setSending(true);
    setError(null);
    setRefused(null);
    setJourney(null);
    setEventId(null);
    try {
      const body = await api<any>("/events", {
        method: "POST",
        body: JSON.stringify({
          occurred_at: new Date().toISOString(),
          app,
          raw_asr: text,
          formatted_text: text,
          committed_text: text,
        }),
      });
      if (body.id) {
        setEventId(body.id);
        // Emptied so the next one can be said immediately. Several takes
        // then one close is the normal shape of a sitting.
        setText("");
      } else setRefused(body);
      setPulse((n) => n + 1);
    } catch (problem: any) {
      setError(problem.message ?? "could not send that");
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <div className="box">
        <textarea
          rows={3}
          value={text}
          placeholder="say something, the way you would into any app…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
          }}
        />
        <div className="apps">
          <span className="micro in-app">in</span>
          {APPS.map((name) => (
            <button
              key={name}
              className="pill"
              aria-pressed={app === name}
              onClick={() => setApp(name)}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      <div className="actions">
        <button
          className="pill send"
          onClick={send}
          disabled={sending || !text.trim()}
        >
          {sending ? "sending\u2026" : "dictate"}
          {/* On the button rather than beside it. The same action named
              twice, side by side, reads as two different actions. */}
          {!sending && <kbd className="keys">⌘↩</kbd>}
        </button>
        {error && <span className="error">{error}</span>}
      </div>

      <Sitting pulse={pulse} onChange={() => setPulse((n) => n + 1)} />

      {refused && <RefusedFlow refused={refused} />}
      {journey && <JourneyFlow journey={journey} />}
    </>
  );
}

/* --- the sitting you are in ---------------------------------------------- */
/*
 * An episode closes after twenty minutes of quiet, which is right for a
 * working day and useless while someone is watching. So this shows what is
 * still open, what the queue owes, and offers to close it now.
 */

function Sitting({ pulse, onChange }: { pulse: number; onChange: () => void }) {
  const [live, setLive] = useState<any>(null);
  const [closing, setClosing] = useState(false);
  const [said, setSaid] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const body = await api("/episodes/live");
        if (alive) setLive(body);
      } catch {
        /* the API restarting should not blank the panel */
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [pulse]);

  const finish = async () => {
    setClosing(true);
    setSaid(null);
    try {
      const body = await api<any>("/episodes/close-open", { method: "POST" });
      setSaid(
        body.closed
          ? "closed — reading it now"
          : body.reason ?? "nothing was waiting",
      );
      onChange();
    } finally {
      setClosing(false);
    }
  };

  if (!live) return null;

  const open = live.open;
  const working = live.queue.filter((j: any) => j.stage !== "profile_refresh");
  const busy = working.length > 0 || live.unassigned > 0;

  return (
    <section className="sitting">
      <div className="sitting-head">
        <span className="micro heading">what is still in flight</span>
        <button
          className="pill finish"
          onClick={finish}
          disabled={closing || (!open && !busy)}
        >
          {closing ? "closing…" : "finish this episode now"}
        </button>
      </div>

      {said && <p className="note">{said}</p>}

      {open ? (
        <div className="stretch">
          <p className="small">
            <Countdown closes={open.closes} count={open.event_count} />
          </p>
          <div className="rows">
            {open.takes?.map((take: any) => (
              <div className="row take" key={take.id}>
                <span className="take-text">{take.text}</span>
                <span className="mono take-meta">
                  {take.app ?? "—"}
                  {take.ingest_status === "ignored" &&
                    ` · not kept (${take.ignore_reason?.replace(/_/g, " ")})`}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="muted small">
          {busy
            ? "nothing open — the queue is still working through what you said"
            : "nothing open. the next thing you say starts a new stretch."}
        </p>
      )}

      {live.unassigned > 0 && (
        <p className="muted small">
          {live.unassigned} take{live.unassigned === 1 ? "" : "s"} not filed
          into a stretch yet — closing will file them first, so nothing is
          left behind.
        </p>
      )}

      {working.length > 0 && (
        <div className="rows queue">
          {working.map((job: any) => (
            <div className="row job" key={job.id}>
              <span className="tag">{job.stage.replace(/_/g, " ")}</span>
              <span className="job-about">
                {job.about ?? job.status}
                {job.due_in_seconds > 0 && ` · waiting ${job.due_in_seconds}s`}
                {job.attempts > 0 && ` · attempt ${job.attempts + 1}`}
              </span>
            </div>
          ))}
        </div>
      )}

      {live.finished?.length > 0 && (
        <div className="rows done">
          {live.finished.map((ep: any) => (
            <div className="row ended-row" key={ep.id}>
              <span className="ended-title">
                {ep.title ?? (ep.waiting_to_be_read ? "not read yet" : "—")}
              </span>
              <span className="mono ended-meta">
                {ep.event_count} take{ep.event_count === 1 ? "" : "s"}
                {ep.beliefs > 0 && ` · ${ep.beliefs} belief${ep.beliefs === 1 ? "" : "s"}`}
                {ep.closed_by === "hand" && " · you ended it"}
              </span>
            </div>
          ))}
        </div>
      )}

      <Json value={live} label="the whole thing as JSON" />
    </section>
  );
}

function RefusedFlow({ refused }: { refused: any }) {
  return (
    <section className="flow">
      <h2 className="micro heading">what happened to it</h2>
      <Step label="heard" status="done" summary="the text reached the system" />
      <Step
        label="refused at the door"
        status="refused"
        summary={`recognised as ${String(refused.category).replace(/_/g, " ")}`}
        detail={refused}
      >
        <p className="note">
          Nothing was stored — not the text, not a redacted copy. The ignore
          log records that something was declined here, at this time, under
          this rule, and nothing about what it said. Keeping the content in
          order to show it to you later would be the same as not refusing it.
        </p>
      </Step>
      <Step label="nothing remembered" status="done" summary="no event, no episode, no belief" />
    </section>
  );
}

function JourneyFlow({ journey }: { journey: any }) {
  const { event, episode, memories, declined } = journey;
  const ignored = event.ingest_status === "ignored";

  return (
    <section className="flow">
      <h2 className="micro heading">what happened to it</h2>

      <Step
        label="stored"
        status="done"
        summary={`as a take in ${event.app ?? "no app"}`}
        detail={event}
      />

      <Step
        label={ignored ? "not worth indexing" : "indexed"}
        status={ignored ? "refused" : event.embedded ? "done" : "running"}
        summary={
          ignored
            ? `${String(event.ignore_reason).replace(/_/g, " ")} — kept, but not searchable`
            : event.embedded
              ? "embedded locally, findable by meaning"
              : "waiting for the embedder"
        }
      >
        {ignored && (
          <p className="note">
            The dictation is still stored and still findable by time and app.
            Only the vector is withheld, because indexing filler makes it
            compete with things that matter.
          </p>
        )}
      </Step>

      {episode ? (
        <Step
          label={episode.status === "open" ? "waiting in an episode" : "episode closed"}
          status={episode.status === "open" ? "waiting" : "done"}
          summary={
            episode.status === "open" ? (
              <Countdown closes={episode.closes} count={episode.event_count} />
            ) : (
              `${episode.event_count} takes together${episode.title ? ` — ${episode.title}` : ""}`
            )
          }
          detail={episode}
        >
          {episode.status === "open" && (
            <p className="note">
              Nothing is read until the stretch of activity finishes. An
              episode closes after {episode.limits.idle_minutes} minutes of
              silence, or once it has run {episode.limits.max_span_hours}{" "}
              hours, or at {episode.limits.max_events} takes — whichever comes
              first. Saying something else now pushes the silence deadline out.
            </p>
          )}
        </Step>
      ) : (
        <Step label="not yet grouped" status="waiting" summary="waiting to be put in an episode" />
      )}

      <Step
        label={
          episode?.summary_status
            ? `read — ${String(episode.summary_status).replace(/_/g, " ")}`
            : "not read yet"
        }
        status={episode?.summary_status ? "done" : "waiting"}
        summary={
          episode?.summary_status
            ? episode.summary_status === "generated"
              ? "a model read the episode and extracted what was durable"
              : episode.summary_status === "verbatim"
                ? "kept as written — no model call was worth it"
                : "skipped — nothing durable in it"
            : "happens once the episode closes"
        }
        detail={episode?.summary ? { summary: episode.summary, tags: episode.topic_tags } : undefined}
      />

      {memories.length > 0 && (
        <Step
          label={`remembered — ${memories.length}`}
          status="done"
          summary={memories.map((m: any) => m.content).join(" · ")}
          detail={memories}
        >
          <div className="rows">
            {memories.map((memory: any) => (
              <div className="row belief" key={memory.id}>
                <span className="belief-text">{memory.content}</span>
                <span className="mono belief-meta">
                  {memory.type} · {memory.status}
                </span>
              </div>
            ))}
          </div>
        </Step>
      )}

      {declined.length > 0 && (
        <Step
          label={`declined — ${declined.length}`}
          status="refused"
          summary={declined.map((d: any) => d.rule.replace(/_/g, " ")).join(", ")}
          detail={declined}
        />
      )}

      {episode?.summary_status && memories.length === 0 && declined.length === 0 && (
        <Step
          label="nothing kept"
          status="done"
          summary="it was read, and held nothing worth remembering"
        />
      )}
    </section>
  );
}

function Countdown({ closes, count }: { closes: any; count: number }) {
  const [left, setLeft] = useState(closes?.in_seconds ?? 0);
  const target = useRef(0);
  target.current = Date.now() + (closes?.in_seconds ?? 0) * 1000;

  useEffect(() => {
    const timer = setInterval(
      () => setLeft(Math.max(0, Math.round((target.current - Date.now()) / 1000))),
      500,
    );
    return () => clearInterval(timer);
  }, []);

  if (!closes) return <>with {count} others</>;
  const mins = Math.floor(left / 60);
  const secs = left % 60;
  return (
    <>
      {count} take{count === 1 ? "" : "s"} so far — closes in{" "}
      <span className="mono">
        {mins}:{String(secs).padStart(2, "0")}
      </span>{" "}
      unless something else is said
    </>
  );
}

/* --- asking -------------------------------------------------------------- */

function Asking() {
  const [text, setText] = useState("");
  const [url, setUrl] = useState<string | null>(null);
  const [steps, setSteps] = useState<any[]>([]);
  const [result, setResult] = useState<any>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const onFrame = useCallback((frame: Frame) => {
    if (frame.event === "step") setSteps((cur) => [...cur, frame.data]);
    if (frame.event === "answer") setResult(frame.data);
    if (frame.event === "error") setFailed(frame.data.error);
  }, []);

  const state = useEventStream(url, onFrame, ASK_FRAMES);
  const running = url !== null && state === "open";

  const ask = () => {
    const said = text.trim();
    if (!said) return;
    setSteps([]);
    setResult(null);
    setFailed(null);
    setUrl(`/stream/ask?request=${encodeURIComponent(said)}&_=${Date.now()}`);
  };

  const answer = result?.steps?.find((s: any) => s.result?.answer)?.result;
  const citations = answer?.citations ?? [];

  return (
    <>
      <div className="box">
        <textarea
          rows={2}
          value={text}
          placeholder="what did we settle on for pricing?"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              ask();
            }
          }}
        />
      </div>

      <div className="actions">
        <button className="pill send" onClick={ask} disabled={running || !text.trim()}>
          {running ? "thinking…" : "ask"}
        </button>
        {failed && <span className="error">{failed}</span>}
      </div>

      {answer && (
        <section className={`answered${answer.answered ? "" : " declined"}`}>
          <p className="answer-text">{answer.answer}</p>

          {!answer.answered && (
            <p className="note">
              Saying nothing is a result. The sources found were on the topic
              but did not contain the answer, and a fluent reply assembled
              from them would be indistinguishable from a real one.
            </p>
          )}
          {answer.superseded_note && (
            <p className="changed">
              <span className="micro">what changed</span>
              {answer.superseded_note}
            </p>
          )}
          {answer.filters_not_applied?.length > 0 && (
            <p className="caveat">
              {answer.filters_not_applied.join(", ")} — asked for, but not
              applied, so the answer is not narrowed by it.
            </p>
          )}

          {citations.length > 0 && (
            <>
              <h3 className="micro heading">
                said because you said {citations.length === 1 ? "this" : "these"}
              </h3>
              <div className="rows cites">
                {citations.map((source: any) => (
                  <div className="row cite" key={source.number}>
                    <span className="mono num">{source.number}</span>
                    <span className="cite-text">
                      {source.text}
                      <span className="cite-meta">
                        {source.kind}
                        {source.occurred_at &&
                          ` · ${new Date(source.occurred_at).toLocaleDateString(
                            undefined,
                            { day: "numeric", month: "short", year: "numeric" },
                          )}`}
                      </span>
                    </span>
                    {source.superseded && <span className="mono replaced">replaced</span>}
                  </div>
                ))}
              </div>
            </>
          )}
          <Json value={answer} label="the full answer payload" />
        </section>
      )}

      {(steps.length > 0 || result) && (
        <section className="flow">
          <h2 className="micro heading">how kivi worked it out</h2>

          {steps.map((step) => (
            <Step
              key={step.seq}
              label={STAGE_NAMES[step.stage] ?? step.stage.replace(/_/g, " ")}
              status="done"
              summary={
                <>
                  {step.decision}
                  {step.latency_ms ? (
                    <span className="mono timing"> {step.latency_ms} ms</span>
                  ) : (
                    <span className="mono timing free"> no model</span>
                  )}
                </>
              }
              detail={step}
            >
              {step.output?.funnel && (
                <Funnel
                  funnel={step.output.funnel}
                  shown={step.output.shown}
                  notShown={step.output.not_shown}
                />
              )}
            </Step>
          ))}

          {running && <Step label="working" status="running" summary="…" />}

          {answer && (
            <Step
              label={answer.answered ? "answered" : "declined"}
              status={answer.answered ? "done" : "refused"}
              summary={
                answer.answered
                  ? `from ${citations.length} source${citations.length === 1 ? "" : "s"} — above`
                  : "nothing found to answer from"
              }
              detail={result}
            />
          )}
        </section>
      )}
    </>
  );
}

const STAGE_NAMES: Record<string, string> = {
  parse: "read the question",
  plan: "decided what to do",
  recall: "searched, then answered",
  find_dictation: "located the take",
  restyle: "rewrote it",
  draft: "composed",
  memory_control: "edited memory",
};
