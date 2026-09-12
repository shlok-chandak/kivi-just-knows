import { useCallback, useEffect, useState } from "react";
import { Trouble } from "../lib/Detail";
import { api } from "../lib/stream";
import "./memory.css";

/* What kivi knows, and what you can do about it.
 *
 * The trust surface. Everything else argues the memory is worth having;
 * this is where you disagree and have it stick.
 *
 * Confidence is shown as a bar and a phrase rather than a number. "0.67"
 * invites a precision the belief does not have -- it is four observations
 * and a prior, not a measurement.
 */

type Row = {
  id: string;
  type: string;
  content: string;
  status: string;
  confidence: number;
  currency: number;
  stale: boolean;
  times_said: number;
  use_count: number;
  last_said: string | null;
  subject: string | null;
  superseded_by: { id: string; content: string | null } | null;
};

type Listing = {
  total: number;
  counts: Record<string, number>;
  memories: Row[];
};

type ProfileEntry = {
  memory_id: string;
  type: string;
  content: string;
  tokens: number;
};

const FILTERS = [
  { id: "live", label: "held" },
  { id: "superseded", label: "history" },
  { id: "candidate", label: "unsure" },
  { id: "all", label: "everything" },
];

const TYPES = ["fact", "decision", "commitment", "preference"];

/* Words, not decimals. The thresholds are the two that already mean
 * something in the system: a single uncorroborated observation, and a
 * claim heard more than once. */
function sureness(row: Row): string {
  if (row.status === "superseded") return "no longer true";
  if (row.status === "candidate") return "still learning this";
  if (row.times_said > 2) return "kivi is confident";
  if (row.times_said > 1) return "kivi is fairly sure";
  return "heard once";
}

export function Memory() {
  const [status, setStatus] = useState("live");
  const [type, setType] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [profile, setProfile] = useState<{
    style: ProfileEntry[];
    work: ProfileEntry[];
    tokens: number;
    budget: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ status, limit: "200" });
    if (type) params.set("type", type);
    if (q.trim()) params.set("q", q.trim());
    setListing(await api<Listing>(`/memories?${params}`));
  }, [status, type, q]);

  useEffect(() => {
    setTrouble(null);
    load().catch((e) => {
      setListing(null);
      setTrouble(String(e?.message ?? e));
    });
  }, [load]);

  useEffect(() => {
    api("/profile").then(setProfile).catch(() => setProfile(null));
  }, []);

  const forget = async (row: Row) => {
    if (!confirm(`Forget "${row.content}"?\n\nThis cannot be undone.`)) return;
    setBusy(true);
    try {
      await api(`/memories/${row.id}`, { method: "DELETE" });
      setOpen(null);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const setProfileState = async (memoryId: string, state: string) => {
    await api("/profile", {
      method: "PATCH",
      body: JSON.stringify({ memory_id: memoryId, state }),
    });
    setProfile(await api("/profile"));
  };

  return (
    <div className="memory">
      <header className="memory-head">
        <h1 className="display">
          everything <span className="swipe">kivi's kept.</span>
        </h1>
        <p className="muted">
          drawn from what you said, and removable when kivi is wrong.
        </p>
      </header>

      <div className="controls">
        <div className="apps">
          {FILTERS.map((filter) => (
            <button
              key={filter.id}
              className="pill"
              aria-pressed={status === filter.id}
              onClick={() => setStatus(filter.id)}
            >
              {filter.label}
              {listing && filter.id !== "all" && filter.id !== "live" && (
                <span className="count"> {listing.counts[filter.id] ?? 0}</span>
              )}
            </button>
          ))}
        </div>
        <input
          type="text"
          className="search"
          placeholder="search what kivi knows…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      <div className="apps types">
        <button className="pill" aria-pressed={!type} onClick={() => setType(null)}>
          all kinds
        </button>
        {TYPES.map((name) => (
          <button
            key={name}
            className="pill"
            aria-pressed={type === name}
            onClick={() => setType(name)}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="memory-body">
        <section>
          {listing && (
            <p className="micro total">
              {listing.total} {listing.total === 1 ? "belief" : "beliefs"}
            </p>
          )}

          {trouble && (
            <Trouble
              error={trouble}
              retry={() => {
                setTrouble(null);
                load().catch((e) => setTrouble(String(e?.message ?? e)));
              }}
            />
          )}

          {!trouble && listing?.memories.length === 0 && (
            <p className="muted idle-note">
              nothing here yet. dictate something and kivi will start listening.
            </p>
          )}

          <div className="rows cards">
            {listing?.memories.map((row) => (
              <article
                key={row.id}
                className={`card ${row.status}`}
                onClick={() => setOpen(open === row.id ? null : row.id)}
              >
                <div className="card-main">
                  <h3 className="claim">{row.content}</h3>
                  <div className="beneath">
                    <span className="muted">{sureness(row)}</span>
                    <span className="bar-track thin">
                      <span
                        className="bar"
                        style={{ width: `${Math.round(row.confidence * 100)}%` }}
                      />
                    </span>
                    <span className="muted">
                      {row.type}
                      {row.times_said > 1 && ` · said ${row.times_said} times`}
                      {row.stale && row.status === "active" && " · a while ago"}
                    </span>
                  </div>

                  {row.superseded_by?.content && (
                    <p className="replaced-by">
                      replaced by <em>{row.superseded_by.content}</em>
                    </p>
                  )}
                </div>
                <span className="chev">{open === row.id ? "↓" : "→"}</span>

                {open === row.id && (
                  <Detail row={row} busy={busy} forget={forget} />
                )}
              </article>
            ))}
          </div>
        </section>

        <aside className="profile">
          <h2 className="micro heading">what rides along with every answer</h2>
          {profile && (
            <p className="small muted budget">
              {profile.tokens} of {profile.budget} tokens
            </p>
          )}
          <div className="rows">
            {[...(profile?.style ?? []), ...(profile?.work ?? [])].map((entry) => (
              <div className="row profile-row" key={entry.memory_id}>
                <span className="profile-text">{entry.content}</span>
                <button
                  className="action"
                  onClick={() => setProfileState(entry.memory_id, "hidden")}
                  title="stop sending this with every answer"
                >
                  hide
                </button>
              </div>
            ))}
          </div>
          {profile && profile.style.length + profile.work.length === 0 && (
            <p className="muted small">nothing is being carried yet.</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function Detail({
  row,
  busy,
  forget,
}: {
  row: Row;
  busy: boolean;
  forget: (row: Row) => void;
}) {
  const [evidence, setEvidence] = useState<any>(null);

  useEffect(() => {
    api(`/memories/${row.id}/provenance`).then(setEvidence).catch(() => setEvidence(null));
  }, [row.id]);

  return (
    <div className="detail" onClick={(e) => e.stopPropagation()}>
      <h4 className="micro">because you said</h4>
      {!evidence && <p className="muted small">looking…</p>}
      <div className="rows">
        {evidence?.evidence?.map((item: any) => (
          <div className="row said-row" key={item.event_id}>
            <span className="said-text">“{item.excerpt ?? item.said}”</span>
            <span className="mono said-meta">
              {item.app ?? "—"}
              {item.occurred_at &&
                ` · ${new Date(item.occurred_at).toLocaleDateString(undefined, {
                  day: "numeric",
                  month: "short",
                })}`}
            </span>
          </div>
        ))}
      </div>
      <div className="detail-foot">
        <span className="muted small">
          used in {row.use_count} {row.use_count === 1 ? "answer" : "answers"}
        </span>
        <button
          className="action forget"
          disabled={busy}
          onClick={() => forget(row)}
        >
          forget this
        </button>
      </div>
    </div>
  );
}
