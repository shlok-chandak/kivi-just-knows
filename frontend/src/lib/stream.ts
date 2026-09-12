import { useEffect, useRef, useState } from "react";

/* Server-sent events, once, for every screen that watches something.
 *
 * Both streams the API offers speak the same shape -- named frames with a
 * JSON body -- so watching a corpus land and watching a question be
 * answered are the same problem. Writing this twice is how the two would
 * drift apart, and the second one would be the one nobody tested.
 *
 * The callback lives in a ref on purpose. Passed straight into the effect
 * it would be a new function on every render, so the stream would close
 * and reopen continuously -- which looks like a working connection while
 * quietly losing every frame that arrives during a reconnect.
 */

export type Frame = { event: string; data: any };

export type StreamState = "idle" | "open" | "closed" | "error";

export function useEventStream(
  url: string | null,
  onFrame: (frame: Frame) => void,
  events: string[],
) {
  const [state, setState] = useState<StreamState>("idle");
  const handler = useRef(onFrame);
  handler.current = onFrame;

  // Joined so the effect compares by value: a fresh array literal each
  // render is a new identity and would reconnect on every keystroke.
  const names = events.join(",");

  useEffect(() => {
    if (!url) {
      setState("idle");
      return;
    }

    const source = new EventSource(url);
    let closed = false;

    source.onopen = () => setState("open");
    source.onerror = () => {
      // A stream the server finished looks identical to one that broke.
      // EventSource retries on its own, so only report a real failure.
      if (source.readyState === EventSource.CLOSED) {
        setState(closed ? "closed" : "error");
      }
    };

    for (const name of names.split(",")) {
      source.addEventListener(name, (message) => {
        let data: any = null;
        try {
          data = JSON.parse((message as MessageEvent).data);
        } catch {
          return;
        }
        // Both of these end the stream. An `error` frame used to be left
        // open, and because the server returns straight after sending one,
        // the browser saw a dropped connection and reconnected -- re-running
        // the whole question, model calls included, every few seconds until
        // the screen was left. A terminal frame has to close the source.
        //
        // A parse failure above returns early, so the native EventSource
        // "error" event (which carries no data) never reaches this.
        if (name === "done" || name === "error") {
          closed = true;
          setState(name === "done" ? "closed" : "error");
          source.close();
        }
        handler.current({ event: name, data });
      });
    }

    return () => {
      closed = true;
      source.close();
    };
  }, [url, names]);

  return state;
}

/** POST/GET JSON, with the error body surfaced rather than swallowed. */
export async function api<T = any>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const body = await response.text();

  // Parse defensively, and only after the status is known. Parsing first
  // meant a proxy's HTML 502 threw "Unexpected token '<'" -- a message about
  // this function rather than about what went wrong, and never the status
  // line the branch below was written to produce.
  let parsed: any = null;
  try {
    parsed = body ? JSON.parse(body) : null;
  } catch {
    if (response.ok) throw new Error(`${path} did not return JSON`);
  }

  if (!response.ok) {
    throw new Error(parsed?.detail ?? `${response.status} ${response.statusText} — ${path}`);
  }
  return parsed as T;
}
