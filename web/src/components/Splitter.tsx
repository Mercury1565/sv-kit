import { useRef } from "react";

/**
 * A vertical drag handle that resizes the column to its left. Drag, or focus it and use ←/→
 * (Shift for bigger steps). Double-click restores the default width.
 */
export function Splitter({
  value,
  min,
  max,
  onChange,
  onReset,
  label,
  style,
}: {
  value: number;
  min: number;
  max: () => number;
  onChange: (w: number) => void;
  onReset: () => void;
  label: string;
  style?: React.CSSProperties;
}) {
  const drag = useRef<{ x: number; w: number } | null>(null);
  const clamp = (w: number) => Math.round(Math.max(min, Math.min(Math.max(min, max()), w)));
  return (
    <div
      className="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={min}
      tabIndex={0}
      title={`${label}: drag to resize, double-click to reset`}
      style={style}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, w: value };
        document.body.classList.add("col-resizing");
      }}
      onPointerMove={(e) => {
        if (drag.current) onChange(clamp(drag.current.w + e.clientX - drag.current.x));
      }}
      onPointerUp={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
        document.body.classList.remove("col-resizing");
      }}
      onPointerCancel={() => {
        drag.current = null;
        document.body.classList.remove("col-resizing");
      }}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 64 : 16;
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          e.stopPropagation(); // the waveform grid uses ←/→ for "previous/next change"
          onChange(clamp(value + (e.key === "ArrowLeft" ? -step : step)));
        } else if (e.key === "Home" || e.key === "End") {
          e.preventDefault();
          e.stopPropagation();
          onChange(clamp(e.key === "Home" ? min : max()));
        }
      }}
    />
  );
}
