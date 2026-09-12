import { useEffect, useState } from "react";
import { Json, Trouble } from "../lib/Detail";
import { api } from "../lib/stream";
import "./profile.css";

/* What rides along with every answer.
 *
 * Not a settings page. These are beliefs the system drew from what was
 * said, promoted because they describe the person rather than the work --
 * so each one traces back to dictations the same way any other memory
 * does. The point of showing it is that a profile assembled silently is
 * one nobody can correct.
 */

type Entry = {
  memory_id: string;
  type: string;
  content: string;
  score: number;
  tokens: number;
};

type Loaded = {
  style: Entry[];
  work: Entry[];
  tokens: number;
  budget: number;
  rendered: string;
};

export function Profile() {
  const [profile, setProfile] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [trouble, setTrouble] = useState<string | null>(null);

  const load = () =>
    api<Loaded>("/profile")
      .then((body) => {
        setProfile(body);
        setTrouble(null);
      })
      .catch((e) => {
        setProfile(null);
        setTrouble(String(e?.message ?? e));
      });

  useEffect(() => {
    load();
  }, []);

  const set = async (memoryId: string, state: string) => {
    setBusy(memoryId);
    setError(null);
    try {
      await api("/profile", {
        method: "PATCH",
        body: JSON.stringify({ memory_id: memoryId, state }),
      });
      await load();
    } catch (problem: any) {
      setError(problem.message ?? "could not change that");
    } finally {
      setBusy(null);
    }
  };

  const used = profile ? Math.round((profile.tokens / profile.budget) * 100) : 0;

  return (
    <div className="profile-screen">
      <header className="profile-head">
        <h1 className="display">
          how kivi thinks <span className="swipe">you sound.</span>
        </h1>
        <p className="muted">
          drawn from what you dictated, not from a form you filled in — and
          sent with every answer kivi writes.
        </p>
      </header>

      {trouble && <Trouble error={trouble} retry={load} />}

      {profile && (
        <div className="budget-strip">
          <div className="budget-bar">
            <div className="budget-fill" style={{ width: `${used}%` }} />
          </div>
          <span className="muted small">
            {profile.tokens} of {profile.budget} tokens used. past the budget
            the weakest entries are dropped, so pinning something costs
            something else its place.
          </span>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <Group
        title="your preferences"
        blurb="style preferences, applied when kivi drafts or rewrites anything in your voice."
        entries={profile?.style ?? []}
        busy={busy}
        set={set}
      />

      <Group
        title="what you're working on"
        blurb="standing facts about the work, so an answer does not have to be told the context each time."
        entries={profile?.work ?? []}
        busy={busy}
        set={set}
      />

      {profile && (
        <section className="rendered">
          <h2 className="micro heading">exactly what gets sent</h2>
          <p className="muted small">
            this text, verbatim, is prepended to every question. nothing is
            summarised on the way.
          </p>
          <pre>{profile.rendered || "(nothing yet)"}</pre>
          <Json value={profile} label="the raw profile payload" />
        </section>
      )}
    </div>
  );
}

function Group({
  title,
  blurb,
  entries,
  busy,
  set,
}: {
  title: string;
  blurb: string;
  entries: Entry[];
  busy: string | null;
  set: (id: string, state: string) => void;
}) {
  return (
    <section className="group-block">
      <h2 className="micro heading">{title}</h2>
      <p className="muted small blurb">{blurb}</p>

      {entries.length === 0 ? (
        <p className="muted idle-note">
          nothing here yet. kivi needs to hear something more than once before
          carrying it around.
        </p>
      ) : (
        <div className="rows">
          {entries.map((entry) => (
            <div className="row entry" key={entry.memory_id}>
              <span className="entry-text">{entry.content}</span>
              <span className="mono entry-cost">{entry.tokens}t</span>
              <span className="entry-actions">
                <button
                  className="action"
                  disabled={busy === entry.memory_id}
                  onClick={() => set(entry.memory_id, "pinned")}
                  title="always keep this, whatever the ranking says"
                >
                  pin
                </button>
                <button
                  className="action hide"
                  disabled={busy === entry.memory_id}
                  onClick={() => set(entry.memory_id, "hidden")}
                  title="stop sending this with every answer"
                >
                  hide
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
