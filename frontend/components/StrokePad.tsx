"use client";
import { useRef } from "react";
import { normalize, simplify, clampPoint, type Pt } from "../lib/strokes";

/** Square drawing pad; each finished stroke is reported in the backend's 0..1000 space. */
export default function StrokePad({ onStroke, disabled }: { onStroke: (s: Pt[]) => void; disabled?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const pts = useRef<{ x: number; y: number }[]>([]);
  const down = useRef(false);

  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return clampPoint(e.clientX - r.left, e.clientY - r.top, r.width);
  };
  const draw = () => {
    const c = ref.current!;
    const g = c.getContext("2d")!;
    const k = c.width / c.getBoundingClientRect().width;
    g.lineWidth = 8; g.lineCap = "round"; g.strokeStyle = "#1d2b22";
    g.beginPath();
    pts.current.forEach((p, i) => (i ? g.lineTo(p.x * k, p.y * k) : g.moveTo(p.x * k, p.y * k)));
    g.stroke();
  };
  const clear = () => ref.current!.getContext("2d")!.clearRect(0, 0, 1000, 1000);

  return (
    <canvas
      ref={ref}
      width={600}
      height={600}
      className="pad"
      data-testid="stroke-pad"
      onPointerDown={(e) => {
        if (disabled) return;
        ref.current!.setPointerCapture(e.pointerId);
        down.current = true;
        pts.current = [pos(e)];
      }}
      onPointerMove={(e) => {
        if (!down.current) return;
        pts.current.push(pos(e));
        draw();
      }}
      onPointerUp={() => {
        if (!down.current) return;
        down.current = false;
        const size = ref.current!.getBoundingClientRect().width;
        const s = simplify(normalize(pts.current, size));
        pts.current = [];
        clear();
        if (s.length >= 2) onStroke(s);
      }}
    />
  );
}
