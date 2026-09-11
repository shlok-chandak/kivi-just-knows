import { useCallback, useRef, useState } from "react";
import { Evaluation } from "./screens/Evaluation";
import { Ignored } from "./screens/Ignored";
import { Memory } from "./screens/Memory";
import { Profile } from "./screens/Profile";
import { Talk } from "./screens/Talk";
import { Upload } from "./screens/Upload";
import "./styles/tokens.css";
import "./styles/shell.css";

/* The frame every screen sits in.
 *
 * Sidebar, then content. No router: a handful of screens with no deep
 * links and no back-button expectations do not need one, and a dependency
 * that earns nothing is a dependency that costs a reader time.
 */

type Screen = "talk" | "memory" | "profile" | "ignored" | "upload" | "evaluation";

// Saying and being answered -- the two halves of one box.
const SPEAK: { id: Screen; label: string; icon: string }[] = [
  { id: "talk", label: "talk", icon: "●" },
];

// What came of it.
const KNOWS: { id: Screen; label: string; icon: string }[] = [
  { id: "memory", label: "memory", icon: "◈" },
  { id: "profile", label: "profile", icon: "◑" },
  { id: "ignored", label: "not kept", icon: "○" },
];

// Feeding and measuring the thing.
const BENCH: { id: Screen; label: string; icon: string }[] = [
  { id: "upload", label: "upload corpus", icon: "↑" },
  { id: "evaluation", label: "evaluation", icon: "◐" },
];

// Wide enough for the longest nav label, narrow enough to leave the
// content room. Outside this the layout stops being the one that was
// designed, so the drag stops rather than letting it get there.
const MIN_WIDTH = 190;
const MAX_WIDTH = 460;
const DEFAULT_WIDTH = 270;
const REMEMBERED = "kivi:sidebar-width";

function useSidebarWidth() {
  const [width, setWidth] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(REMEMBERED));
      return saved >= MIN_WIDTH && saved <= MAX_WIDTH ? saved : DEFAULT_WIDTH;
    } catch {
      // Private windows and blocked site data both throw on read.
      return DEFAULT_WIDTH;
    }
  });

  const set = useCallback((next: number) => {
    const clamped = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(next)));
    setWidth(clamped);
    try {
      localStorage.setItem(REMEMBERED, String(clamped));
    } catch {
      // Remembering is a convenience; failing to is not worth an error.
    }
  }, []);

  return [width, set] as const;
}

function Grip({ width, set }: { width: number; set: (n: number) => void }) {
  const dragging = useRef(false);

  // Pointer capture rather than window listeners: the handle keeps
  // receiving moves even when the pointer runs ahead of it, and the class
  // on <body> holds the resize cursor and suppresses text selection for
  // the whole drag rather than only over the handle itself.
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragging.current = true;
    document.body.classList.add("resizing");
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    set(event.clientX);
  };

  const stop = (event: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    document.body.classList.remove("resizing");
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  return (
    <div
      className="grip"
      role="separator"
      aria-orientation="vertical"
      aria-label="sidebar width"
      aria-valuenow={width}
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={MAX_WIDTH}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onDoubleClick={() => set(DEFAULT_WIDTH)}
      // A drag handle nobody can reach from the keyboard is a handle half
      // the people using this cannot move.
      onKeyDown={(event) => {
        if (event.key === "ArrowLeft") set(width - (event.shiftKey ? 40 : 10));
        if (event.key === "ArrowRight") set(width + (event.shiftKey ? 40 : 10));
        if (event.key === "Home") set(DEFAULT_WIDTH);
      }}
    >
      <span className="grip-line" />
    </div>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>("talk");
  const [width, setWidth] = useSidebarWidth();

  return (
    <div
      className="shell"
      style={{ ["--sidebar-w" as string]: `${width}px` }}
    >
      <aside className="sidebar">
        <div className="wordmark">
          kiv<span className="dotted">i</span>
        </div>

        <nav>
          {SPEAK.map((item) => (
            <NavItem key={item.id} {...item} screen={screen} go={setScreen} />
          ))}

          <div className="group">
            <span className="micro underlined">what kivi knows</span>
          </div>
          {KNOWS.map((item) => (
            <NavItem key={item.id} {...item} screen={screen} go={setScreen} />
          ))}

          <div className="group">
            <span className="micro underlined">under the hood</span>
          </div>
          {BENCH.map((item) => (
            <NavItem key={item.id} {...item} screen={screen} go={setScreen} />
          ))}
        </nav>

        {/* The empty space is deliberate. Resist filling it. */}
        <div className="grow" />
      </aside>

      <Grip width={width} set={setWidth} />

      <main className="content">
        {screen === "talk" && <Talk />}
        {screen === "memory" && <Memory />}
        {screen === "profile" && <Profile />}
        {screen === "ignored" && <Ignored />}
        {screen === "upload" && <Upload />}
        {screen === "evaluation" && <Evaluation />}
      </main>
    </div>
  );
}

function NavItem({
  id,
  label,
  icon,
  screen,
  go,
}: {
  id: Screen;
  label: string;
  icon: string;
  screen: Screen;
  go: (s: Screen) => void;
}) {
  return (
    <button className="nav" aria-current={screen === id} onClick={() => go(id)}>
      <span className="nav-icon">{icon}</span>
      {label}
    </button>
  );
}
