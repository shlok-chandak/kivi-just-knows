import { useCallback, useEffect, useRef, useState } from "react";
import { Hint, Json, Step } from "../lib/Detail";
import { api, useEventStream, type Frame } from "../lib/stream";
import "./upload.css";

/* Load a corpus and watch it land.
 *
 * Built for somebody arriving with their own data rather than ours, so
 * the format is documented on the page instead of in a README they have
 * not opened. What matters after an upload is not that it succeeded but
 * what was made of it -- so the run ends on the beliefs, not on a count.
 */

const FRAMES = ["hello", "event", "ignored", "stage", "progress", "done"];
const BATCH = 200;

type Counters = {
  events: number;
  episodes: number;
  memories: number;
  ignored_total: number;
  cost_usd: number;
  ignored: Record<string, number>;
  status: { phase: string; detail: string; idle: boolean; waiting: number };
};

export function Upload() {
  const [records, setRecords] = useState<any[] | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<any>(null);
  const [counters, setCounters] = useState<Counters | null>(null);
  const [feed, setFeed] = useState<any[]>([]);
  const [memories, setMemories] = useState<any[] | null>(null);
  const startedAt = useRef<Counters | null>(null);
  const uploadedAt = useRef<string | null>(null);
  const [formed, setFormed] = useState<any>(null);

  const onFrame = useCallback((frame: Frame) => {
    if (frame.event === "hello" || frame.event === "progress") {
      setCounters(frame.data);
      if (!startedAt.current) startedAt.current = frame.data;
      return;
    }
    setFeed((cur) => [frame, ...cur].slice(0, 200));
  }, []);

  useEventStream("/stream/ingest", onFrame, FRAMES);

  // Once the queue drains, show what the corpus actually became. That is
  // the question an upload is really asking.
  const idle = counters?.status?.idle;
  useEffect(() => {
    if (!sent || !idle) return;
    api("/memories?status=all&limit=300").then((body: any) => setMemories(body.memories));
    if (uploadedAt.current) {
      api(`/episodes/formed?since=${encodeURIComponent(uploadedAt.current)}&limit=40`)
        .then(setFormed)
        .catch(() => setFormed(null));
    }
  }, [sent, idle]);

  const choose = async (file: File) => {
    setFilename(file.name);
    setParseError(null);
    setRecords(null);
    try {
      const raw = (await file.text()).trim();
      const parsed = raw.startsWith("[")
        ? JSON.parse(raw)
        : raw.split("\n").filter(Boolean).map((line, i) => {
            try {
              return JSON.parse(line);
            } catch {
              throw new Error(`line ${i + 1} is not valid JSON`);
            }
          });
      if (!Array.isArray(parsed) || parsed.length === 0) {
        throw new Error("no records found");
      }
      const missing = parsed.findIndex((r) => !r.occurred_at);
      if (missing >= 0) {
        throw new Error(`record ${missing + 1} has no occurred_at`);
      }
      setRecords(parsed);
    } catch (problem: any) {
      setParseError(problem.message ?? "could not read that file");
    }
  };

  const upload = async () => {
    if (!records) return;
    setSending(true);
    setMemories(null);
    setFormed(null);
    // A second before the first record lands, so nothing is missed to a
    // clock that disagrees with the server's by a hair.
    uploadedAt.current = new Date(Date.now() - 1000).toISOString();
    let stored = 0;
    let refused = 0;
    try {
      for (let i = 0; i < records.length; i += BATCH) {
        const body = await api<any>("/events/batch", {
          method: "POST",
          body: JSON.stringify({ events: records.slice(i, i + BATCH) }),
        });
        stored += body.stored;
        refused += body.refused;
      }
      setSent({ received: records.length, stored, refused });
    } catch (problem: any) {
      setParseError(problem.message ?? "the upload was rejected");
    } finally {
      setSending(false);
    }
  };

  const made = startedAt.current && counters
    ? {
        events: counters.events - startedAt.current.events,
        episodes: counters.episodes - startedAt.current.episodes,
        memories: counters.memories - startedAt.current.memories,
        ignored: counters.ignored_total - startedAt.current.ignored_total,
      }
    : null;

  return (
    <div className="upload">
      <header className="upload-head">
        <h1 className="display">
          bring your <span className="swipe">own corpus.</span>
        </h1>
        <p className="muted">
          upload a file of dictations and watch what kivi keeps, what it
          declines, and what it ends up believing.
        </p>
      </header>

      <div className="picker">
        <label className="file-btn">
          <input
            type="file"
            accept=".jsonl,.json"
            onChange={(e) => e.target.files?.[0] && choose(e.target.files[0])}
          />
          choose a file
        </label>

        <span className="picked muted">
          {filename
            ? records
              ? `${filename} — ${records.length} records`
              : filename
            : "nothing chosen"}
        </span>

        <button
          className="pill send"
          onClick={upload}
          disabled={!records || sending}
        >
          {sending ? "uploading…" : "upload and watch"}
        </button>

        <Hint title="what the file should look like">
          <FormatHelp />
        </Hint>
      </div>

      {parseError && <p className="error">{parseError}</p>}

      {sent && (
        <section className="flow">
          <h2 className="micro heading">what happened</h2>

          <Step
            label="read"
            status="done"
            summary={`${sent.received} records in the file`}
            detail={sent}
          />
          <Step
            label="stored"
            status="done"
            summary={`${sent.stored} kept as takes`}
          />
          {sent.refused > 0 && (
            <Step
              label="refused at the door"
              status="refused"
              summary={`${sent.refused} looked like private details and were never written down`}
            >
              <p className="note">
                Refused before storage, so there is no text to show — only
                that something was declined. The whole promise is that it was
                never kept.
              </p>
            </Step>
          )}
          <Step
            label={idle ? "read and understood" : "working through it"}
            status={idle ? "done" : "running"}
            summary={
              idle
                ? formed
                  // Counted from the server, for this upload. The delta below
                  // is measured from when the screen was opened, so a second
                  // upload in the same sitting reported the first one's work
                  // -- or, if the screen was opened after the fact, zero.
                  ? `${formed.episodes.length} episode${
                      formed.episodes.length === 1 ? "" : "s"
                    }, ${formed.beliefs} belief${
                      formed.beliefs === 1 ? "" : "s"
                    } formed`
                  : "finished"
                : `${counters?.status.phase ?? ""} — ${counters?.status.detail ?? ""}`
            }
            detail={counters?.status}
          >
            {formed?.episodes?.length > 0 && (
              <div className="formed">
                <p className="small muted">
                  Each stretch below was closed, read once, and what it
                  yielded is underneath it. An episode with no belief restated
                  something already known.
                </p>
                {formed.episodes.map((episode: any) => (
                  <div className="formed-ep" key={episode.id}>
                    <p className="formed-title">
                      {episode.title ?? "not read yet"}
                      <span className="mono formed-meta">
                        {episode.event_count} take
                        {episode.event_count === 1 ? "" : "s"} ·{" "}
                        {episode.beliefs.length} belief
                        {episode.beliefs.length === 1 ? "" : "s"}
                        {episode.topic_tags?.length
                          ? ` · ${episode.topic_tags.join(", ")}`
                          : ""}
                      </span>
                    </p>

                    <div className="formed-takes">
                      {episode.takes.map((take: any) => (
                        <p className="formed-take" key={take.id}>
                          {take.text}
                          {take.app && (
                            <span className="mono formed-app">{take.app}</span>
                          )}
                        </p>
                      ))}
                    </div>

                    {episode.beliefs.length > 0 ? (
                      <div className="rows formed-beliefs">
                        {episode.beliefs.map((belief: any) => (
                          <div className="row formed-belief" key={belief.id}>
                            <span className="mono belief-type">{belief.type}</span>
                            <span className="belief-text">{belief.text}</span>
                            <span className="mono belief-conf">
                              {Math.round(belief.confidence * 100)}%
                              {belief.superseded && " · replaced"}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="small muted formed-none">
                        nothing new drawn from this one
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Step>
        </section>
      )}

      {sent && (
        <div className="watch">
          <section>
            <h2 className="micro heading">as it lands</h2>
            <div className="live-feed">
              {feed.length === 0 && (
                <p className="muted small">waiting for the first one…</p>
              )}
              {feed.map((frame, i) => (
                <FeedLine key={i} frame={frame} />
              ))}
            </div>
          </section>

          <aside className="tally">
            <Tally label="takes" value={counters?.events} delta={made?.events} />
            <Tally label="episodes" value={counters?.episodes} delta={made?.episodes} />
            <Tally label="beliefs" value={counters?.memories} delta={made?.memories} />
            <Tally label="not kept" value={counters?.ignored_total} delta={made?.ignored} />
            <Tally
              label="spent"
              value={counters ? `$${counters.cost_usd.toFixed(4)}` : undefined}
            />
            <h3 className="micro heading">not kept, by rule</h3>
            {Object.entries(counters?.ignored ?? {})
              .sort((a, b) => b[1] - a[1])
              .map(([rule, n]) => (
                <div className="rule" key={rule}>
                  <span>{rule.replace(/_/g, " ")}</span>
                  <span className="mono">{n}</span>
                </div>
              ))}
          </aside>
        </div>
      )}

      {memories && (
        <section className="ended">
          <h2 className="section">what kivi believes now</h2>
          <p className="muted small">
            everything held after this corpus, newest first. history is
            included and marked.
          </p>
          <div className="rows">
            {memories.slice(0, 60).map((memory: any) => (
              <div className={`row made ${memory.status}`} key={memory.id}>
                <span className="made-text">{memory.content}</span>
                <span className="mono made-meta">
                  {memory.type}
                  {memory.status === "superseded" && " · replaced"}
                  {memory.status === "candidate" && " · unsure"}
                </span>
              </div>
            ))}
          </div>
          <Json value={memories} label={`all ${memories.length} as JSON`} />
        </section>
      )}
    </div>
  );
}

function Tally({
  label,
  value,
  delta,
}: {
  label: string;
  value?: number | string;
  delta?: number;
}) {
  return (
    <div className="tally-row">
      <span className="muted">{label}</span>
      <span className="tally-figure">
        {value ?? "–"}
        {delta !== undefined && delta > 0 && (
          <span className="delta mono">+{delta}</span>
        )}
      </span>
    </div>
  );
}

function FeedLine({ frame }: { frame: Frame }) {
  const { event, data } = frame;
  if (event === "event") {
    // Stored either way. "skipped" means it was left out of the index, so it
    // can still be found by time and app but never by meaning -- which is a
    // different fate from "kept", and was being reported as the same one.
    const skipped = data.status === "ignored";
    return (
      <div className={`line ${skipped ? "unindexed" : "kept"}`}>
        <span className="tag">{skipped ? "skipped" : "kept"}</span>
        <span className="line-text">
          {data.text}
          {skipped && (
            <span className="claim-why">
              {" "}
              — {String(data.ignore_reason ?? "not worth indexing").replace(/_/g, " ")},
              stored but not searchable by meaning
            </span>
          )}
        </span>
        <span className="mono line-app">{data.app}</span>
      </div>
    );
  }
  if (event === "ignored") {
    return (
      <div className="line dropped">
        <span className="tag">{data.rule.replace(/_/g, " ")}</span>
        <span className="line-text">
          {data.withheld ? (
            <em className="withheld">content withheld — showing it would undo the refusal</em>
          ) : (
            data.rationale
          )}
        </span>
      </div>
    );
  }
  // Read once, and what came out of it. "generated" and "skipped" are
  // verdicts, and a verdict with nothing under it cannot be checked -- so
  // the rationale and the claims themselves go on the line.
  const claims: any[] = data.claims ?? [];
  return (
    <div className="line stage">
      <div className="stage-line">
        <span className="tag">{data.stage?.replace(/_/g, " ")}</span>
        <span className="line-text">
          {VERDICTS[data.decision] ?? data.decision}
          {data.dictations ? ` · ${data.dictations} read` : ""}
        </span>
      </div>

      {data.title && (
        <p className="stage-title">
          {data.title}
          {data.topic_tags?.length > 0 && (
            <span className="mono stage-tags">{data.topic_tags.join(" · ")}</span>
          )}
        </p>
      )}
      {data.summary && data.summary !== data.title && (
        <p className="stage-summary">{data.summary}</p>
      )}

      {data.rationale && <p className="stage-why">{data.rationale}</p>}

      {claims.length > 0 && (
        <div className="claims">
          {claims.map((claim, i) => (
            <p className={`claim ${claim.outcome}`} key={i}>
              <span className="mono claim-mark">{MARKS[claim.outcome] ?? "·"}</span>
              <span>
                {claim.withheld ? (
                  <em className="withheld">content withheld</em>
                ) : (
                  claim.text
                )}
                {claim.why && <span className="claim-why"> — {claim.why}</span>}
              </span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/* The stored verdict is one word. These say what the word meant. */
const VERDICTS: Record<string, string> = {
  generated: "read and understood",
  verbatim: "kept as said, too short to summarise",
  skipped: "read, nothing worth keeping",
};

const MARKS: Record<string, string> = {
  created: "+",
  reinforced: "=",
  superseded: "~",
  refused: "×",
};

/* The format, documented where somebody uploading will actually see it. */
function FormatHelp() {
  return (
    <>
      <p>
        <strong>JSON Lines</strong> — one dictation per line — or a single
        JSON array. Both are accepted.
      </p>
      <pre>{`{"occurred_at": "2026-06-15T09:12:00+05:30",
 "app": "slack",
 "raw_asr": "move the standup to ten",
 "formatted_text": "Move the standup to 10.",
 "committed_text": "Move the standup to 10."}`}</pre>
      <dl>
        <dt>occurred_at</dt>
        <dd>
          required. ISO 8601 <em>with a timezone</em> — a naive timestamp is
          rejected rather than assumed to be UTC.
        </dd>
        <dt>formatted_text</dt>
        <dd>what the recogniser produced, tidied. this is what gets read.</dd>
        <dt>raw_asr</dt>
        <dd>optional. the untidied transcript.</dd>
        <dt>committed_text</dt>
        <dd>
          optional. what was actually sent after any edit. an empty string
          means it was discarded, and nothing is remembered from it.
        </dd>
        <dt>app</dt>
        <dd>
          optional. where it was said. dictation in an editor is treated as
          an instruction to a tool, so no beliefs are drawn from it.
        </dd>
        <dt>context_hash</dt>
        <dd>
          optional. an opaque id for the window or thread. only ever compared
          for equality, so it groups without revealing anything.
        </dd>
        <dt>asr_confidence · duration_ms</dt>
        <dd>
          optional. used to spot a transcript the recogniser was guessing at,
          or one whose length disagrees with its audio.
        </dd>
      </dl>
      <p className="muted">
        Unknown fields are ignored. Records are upserted, so re-uploading the
        same file will not duplicate anything.
      </p>
    </>
  );
}
