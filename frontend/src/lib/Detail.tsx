import { useState, type ReactNode } from "react";
import "./detail.css";

/* Anything on screen can be opened up to what it actually was.
 *
 * Every summary here is a lossy rendering of a JSON payload, and the
 * summary is a judgement about what matters. When that judgement is
 * wrong -- which is exactly when someone is looking -- the underlying
 * object has to be one click away rather than a curl command away.
 */

export function Step({
  label,
  status = "done",
  summary,
  detail,
  children,
}: {
  label: string;
  status?: "done" | "running" | "waiting" | "refused";
  summary?: ReactNode;
  detail?: unknown;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const openable = detail !== undefined || children !== undefined;

  return (
    <div className={`step-row ${status}`}>
      <span className="rail">
        <span className="node" />
      </span>
      <div className="step-body">
        <button
          className="step-head"
          onClick={() => openable && setOpen(!open)}
          disabled={!openable}
        >
          <span className="step-label">{label}</span>
          {summary && <span className="step-summary muted">{summary}</span>}
          {openable && <span className="disclose muted">{open ? "−" : "+"}</span>}
        </button>
        {open && (
          <div className="step-detail">
            {children}
            {detail !== undefined && <Json value={detail} />}
          </div>
        )}
      </div>
    </div>
  );
}

export function Json({ value, label }: { value: unknown; label?: string }) {
  const [open, setOpen] = useState(!label);
  return (
    <div className="json">
      {label && (
        <button className="action json-toggle" onClick={() => setOpen(!open)}>
          {open ? "hide" : "show"} {label}
        </button>
      )}
      {open && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </div>
  );
}

/* A label with an explanation behind it, for the things a reader cannot
 * be expected to already know -- the shape of an upload, what a rule
 * means. Click rather than hover: hover tooltips are unreachable on a
 * touch screen and vanish while you are still reading them. */
export function Hint({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="hint-wrap">
      <button className="hint-mark" onClick={() => setOpen(!open)} aria-expanded={open}>
        {title}
        <span className="hint-icon">?</span>
      </button>
      {open && <div className="hint-body">{children}</div>}
    </span>
  );
}
