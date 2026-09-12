import { useEffect, useState } from "react";
import { Trouble } from "../lib/Detail";
import { api } from "../lib/stream";
import "./ignored.css";

/* What kivi heard and deliberately did not keep.
 *
 * A memory system is judged as much by what it declines as by what it
 * stores, and a refusal nobody can see is indistinguishable from a bug.
 *
 * Sensitive refusals show when and where but never what. The content is
 * the thing being refused, so storing it in order to display it later
 * would undo the refusal. A visible gap is the honest version.
 */

type Row = {
  id: string;
  rule: string;
  rationale: string | null;
  at: string | null;
  app: string | null;
  occurred_at: string | null;
  candidate: any;
  withheld: boolean;
};

/* The rule, said the way a person would say it. The wording matters more
 * here than anywhere else on the screen: this list is the system
 * explaining itself, and a rule name is not an explanation. */
const RULES: Record<string, string> = {
  sensitive_category: "looked like a private detail",
  sensitive_on_review: "private, and only obvious once kivi had read it",
  no_extractable_content: "nothing durable in it",
  tool_instruction: "an instruction to a tool, not a fact",
  no_content: "nothing but filler",
  low_asr_confidence: "kivi couldn't make it out",
  implausible_timing: "the audio and the words disagreed",
  superseded_by_retry: "you said it again straight after",
  discarded_by_user: "you threw it away yourself",
};

export function Ignored() {
  const [data, setData] = useState<{
    by_rule: Record<string, number>;
    total: number;
    ignored: Row[];
  } | null>(null);
  const [rule, setRule] = useState<string | null>(null);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [again, setAgain] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams({ limit: "200" });
    if (rule) params.set("rule", rule);
    setTrouble(null);
    api(`/ignored?${params}`)
      .then(setData)
      .catch((e) => {
        setData(null);
        setTrouble(String(e?.message ?? e));
      });
  }, [rule, again]);

  return (
    <div className="ignored">
      <header className="ignored-head">
        <h1 className="display">
          kivi heard this and <span className="swipe">didn't keep it.</span>
        </h1>
        <p className="muted">
          every decision not to remember, with the rule that made it.
        </p>
      </header>

      <div className="apps rule-filters">
        <button className="pill" aria-pressed={!rule} onClick={() => setRule(null)}>
          everything
          {data && <span className="count"> {data.total}</span>}
        </button>
        {Object.entries(data?.by_rule ?? {})
          .sort((a, b) => b[1] - a[1])
          .map(([name, n]) => (
            <button
              key={name}
              className="pill"
              aria-pressed={rule === name}
              onClick={() => setRule(name)}
            >
              {RULES[name] ?? name.replace(/_/g, " ")}
              <span className="count"> {n}</span>
            </button>
          ))}
      </div>

      {data?.ignored.length === 0 && (
        <p className="muted idle-note">nothing was turned away.</p>
      )}

      {trouble && <Trouble error={trouble} retry={() => setAgain((n) => n + 1)} />}

      {!trouble && data?.ignored.length === 0 && (
        <p className="muted idle-note">
          nothing was refused. either nothing sensitive has been said, or
          nothing has been said at all.
        </p>
      )}

      <div className="dropped">
        {data?.ignored.map((row) => (
          <article className="turned" key={row.id}>
            <span className={`dot ${row.withheld ? "private" : ""}`} />
            <div className="turned-main">
              {row.withheld ? (
                <p className="gap">
                  content withheld — keeping it in order to show it here
                  would undo the refusal
                </p>
              ) : (
                <p className="turned-text">
                  {row.candidate?.content ??
                    row.candidate?.episode_text ??
                    row.rationale ??
                    "—"}
                </p>
              )}
              <span className="muted reason">
                {RULES[row.rule] ?? row.rule.replace(/_/g, " ")}
              </span>
            </div>
            <span className="mono when">
              {row.app ?? "—"}
              {row.occurred_at &&
                ` · ${new Date(row.occurred_at).toLocaleDateString(undefined, {
                  day: "numeric",
                  month: "short",
                })}`}
            </span>
          </article>
        ))}
      </div>
    </div>
  );
}
