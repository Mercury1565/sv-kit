import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { GLOSSARY, explainText, type TermId } from "../lib/glossary";

/**
 * Small (i) button: hover or keyboard focus shows the explanation; click/tap toggles it (touch);
 * Escape closes it. The bubble is portalled to <body> so table headers and cards never clip it.
 */
export function Info({ term, text }: { term?: TermId; text?: string }) {
  const body = text ?? (term ? explainText(term) : "");
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number; below: boolean } | null>(null);
  const [pinned, setPinned] = useState(false);

  const show = useCallback(() => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const below = r.top < 140;
    setPos({ x: Math.min(Math.max(r.left + r.width / 2, 150), window.innerWidth - 150), y: below ? r.bottom + 6 : r.top - 6, below });
  }, []);
  const hide = useCallback(() => { if (!pinned) setPos(null); }, [pinned]);

  useEffect(() => {
    if (!pos) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e.type === "pointerdown" && btn.current?.contains(e.target as Node)) return;
      setPinned(false);
      setPos(null);
    };
    const reflow = () => setPos(null);
    window.addEventListener("keydown", close);
    window.addEventListener("pointerdown", close);
    window.addEventListener("scroll", reflow, true);
    return () => {
      window.removeEventListener("keydown", close);
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("scroll", reflow, true);
    };
  }, [pos]);

  if (!body) return null;
  return (
    <>
      <button ref={btn} type="button" className="info-btn" aria-label="What does this mean?" aria-describedby={pos ? id : undefined}
        onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
        onClick={(e) => { e.stopPropagation(); setPinned((p) => !p); show(); }}>
        i
      </button>
      {pos && createPortal(
        <span id={id} role="tooltip" className={`info-tip ${pos.below ? "below" : "above"}`} style={{ left: pos.x, top: pos.y }}>
          {body}
        </span>, document.body)}
    </>
  );
}

/** A glossary label followed by its (i), e.g. <Term id="setup_slack" /> -> "Worst setup slack (i)". */
export function Term({ id, children }: { id: TermId; children?: ReactNode }) {
  return (
    <span className="term">
      {children ?? GLOSSARY[id].label}
      <Info term={id} />
    </span>
  );
}

export const termLabel = (id: TermId) => GLOSSARY[id].label;
