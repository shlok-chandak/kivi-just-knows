import { useState } from "react";
import "./funnel.css";

/* The narrowing, and the rows behind each number.
 *
 * Three bars say 17 matched, 8 reached the model, 1 was cited. On their own
 * those are arithmetic nobody can check: the interesting case is the right
 * memory sitting ninth, and a count cannot show it. So every bar opens into
 * the items it counted, with the score and the reason each one was found.
 */

type Row = {
  number: number;
  kind: string;
  text: string;
  occurred_at: string | null;
  superseded?: boolean;
  stale?: boolean;
  similarity?: number;
  score?: number;
  matched_by?: string[];
  memory_type?: string | null;
  confidence?: number | null;
  cited?: boolean;
};

type Tier = "matched" | "shown" | "cited";

const HOW: Record<string, string> = {
  vector: "meaning",
  lexical: "wording",
  recent: "recency",
};

function day(iso: string | null) {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function Funnel({
  funnel,
  shown = [],
  notShown = [],
}: {
  funnel: any;
  shown?: Row[];
  notShown?: Row[];
}) {
  const [open, setOpen] = useState<Tier | null>(null);

  const cited = shown.filter((row) => row.cited);
  const bars: { tier: Tier; label: string; value: number; rows: Row[] }[] = [
    {
      tier: "matched",
      label: "matched",
      value: funnel.matched ?? 0,
      rows: [...shown, ...notShown],
    },
    { tier: "shown", label: "shown to the model", value: funnel.shown ?? 0, rows: shown },
    { tier: "cited", label: "actually cited", value: funnel.cited ?? 0, rows: cited },
  ];
  const widest = Math.max(...bars.map((bar) => bar.value), 1);

  return (
    <div className="funnel">
      {bars.map((bar) => {
        const openable = bar.rows.length > 0;
        const isOpen = open === bar.tier;
        return (
          <div key={bar.tier}>
            <button
              className={`funnel-step${isOpen ? " on" : ""}`}
              onClick={() => setOpen(isOpen ? null : bar.tier)}
              disabled={!openable}
            >
              <span className="muted small">{bar.label}</span>
              <span className="bar-track">
                <span
                  className="bar"
                  style={{ width: `${Math.max((bar.value / widest) * 100, 3)}%` }}
                />
              </span>
              <span className="mono">{bar.value}</span>
              <span className="mono disclose">{openable ? (isOpen ? "−" : "+") : ""}</span>
            </button>
            {isOpen && <Rows rows={bar.rows} total={bar.value} tier={bar.tier} />}
          </div>
        );
      })}
      {funnel.by_kind && (
        <p className="small muted">
          {Object.entries(funnel.by_kind)
            .map(([kind, n]) => `${n} ${kind}${Number(n) === 1 ? "" : "s"}`)
            .join(", ")}
          {funnel.replaced_shown > 0 &&
            ` · ${funnel.replaced_shown} shown as history, labelled replaced`}
        </p>
      )}
    </div>
  );
}

/* The rows are capped on the way out of the API, so a long tail can be
 * counted without being stored. Saying so beats quietly showing fewer. */
function Rows({ rows, total, tier }: { rows: Row[]; total: number; tier: Tier }) {
  return (
    <div className="funnel-rows">
      {rows.map((row) => (
        <div className={`retrieved${row.cited ? " cited" : ""}`} key={`${row.kind}-${row.number}`}>
          <span className="mono num">{row.number}</span>
          <div className="retrieved-body">
            <p className="retrieved-text">{row.text}</p>
            <p className="retrieved-meta mono">
              <span className="kind">{row.memory_type || row.kind}</span>
              {day(row.occurred_at) && <span>{day(row.occurred_at)}</span>}
              {row.similarity !== undefined && (
                <span title="how close the meaning was">
                  match {row.similarity.toFixed(2)}
                </span>
              )}
              {row.score !== undefined && (
                <span title="match, adjusted for age, confidence and past use">
                  rank {row.score.toFixed(2)}
                </span>
              )}
              {row.matched_by?.length ? (
                <span>found by {row.matched_by.map((m) => HOW[m] ?? m).join(" + ")}</span>
              ) : null}
              {row.superseded && <span className="flag replaced">replaced</span>}
              {row.stale && !row.superseded && <span className="flag">not restated lately</span>}
              {row.cited && <span className="flag used">used in the answer</span>}
            </p>
          </div>
        </div>
      ))}
      {total > rows.length && (
        <p className="small muted cap">
          {total - rows.length} more counted but not kept on the trace.
        </p>
      )}
      {tier === "shown" && rows.some((row) => !row.cited) && (
        <p className="small muted cap">
          The rest reached the model and were not used. That is the model
          declining to cite them, not a retrieval miss.
        </p>
      )}
    </div>
  );
}
