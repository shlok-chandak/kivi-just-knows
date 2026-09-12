import type { ReactNode } from "react";
import "./markdown.css";

/* The evaluation write-ups, rendered.
 *
 * These are generated files, and they were being printed into a <pre> --
 * so a table of latency percentiles arrived as a wall of pipe characters
 * that nobody would read past. The numbers are the whole argument of that
 * screen, and a reader has to be able to scan a column.
 *
 * Deliberately not a markdown library. The reports use four constructs --
 * headings, tables, bullet lists, paragraphs -- and this file is smaller
 * than the dependency would be, with no HTML passthrough, so nothing in a
 * report can inject markup into the page.
 */

/** `**bold**` and `` `code` ``, which generated reports may start using. */
function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      parts.push(<strong key={parts.length}>{token.slice(2, -2)}</strong>);
    } else {
      parts.push(
        <code className="mono" key={parts.length}>
          {token.slice(1, -1)}
        </code>,
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function cells(row: string): string[] {
  return row
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** A row of dashes under the header, which marks a table rather than prose. */
function isDivider(line: string): boolean {
  return /^\|[\s|:-]+\|$/.test(line.trim());
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const depth = heading[1].length;
      // The page already has an <h1>. A report's own top heading is a
      // section within it, so everything shifts down one level.
      const Tag = (depth === 1 ? "h2" : "h3") as "h2" | "h3";
      blocks.push(
        <Tag className={depth === 1 ? "md-h1" : "md-h2"} key={blocks.length}>
          {inline(heading[2])}
        </Tag>,
      );
      i += 1;
      continue;
    }

    if (line.trim().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i]);
        i += 1;
      }
      const header = cells(rows[0]);
      const body = rows.slice(isDivider(rows[1] ?? "") ? 2 : 1).map(cells);
      // A leading blank header cell is the row label's column, which has no
      // name in any of these tables. Nothing to render, but the column has
      // to stay so the numbers line up under their headings.
      blocks.push(
        <div className="md-table-wrap" key={blocks.length}>
          <table className="md-table">
            <thead>
              <tr>
                {header.map((cell, n) => (
                  <th key={n} className={n === 0 ? "md-label" : undefined}>
                    {inline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, n) => (
                <tr key={n}>
                  {row.map((cell, m) => (
                    <td key={m} className={m === 0 ? "md-label" : undefined}>
                      {inline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul className="md-list" key={blocks.length}>
          {items.map((item, n) => (
            <li key={n}>{inline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Everything else is a paragraph. Consecutive lines belong to it, but a
    // single newline is kept as a break: these reports put one fact per
    // line and reflowing them into prose would run them together.
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("|") &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i])
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push(
      <p className="md-p" key={blocks.length}>
        {para.map((row, n) => (
          <span key={n}>
            {inline(row)}
            {n < para.length - 1 && <br />}
          </span>
        ))}
      </p>,
    );
  }

  return <div className="md">{blocks}</div>;
}
