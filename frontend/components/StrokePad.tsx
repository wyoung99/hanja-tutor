"use client";
import { useCallback, useEffect, useRef } from "react";
import { normalize, simplify, clampPoint, toCanvas, type Pt } from "../lib/strokes";

const SIZE = 600; // canvas backing pixels; strokes are exchanged in the backend's 0..1000 space

type Props = {
  onStroke: (s: Pt[]) => void;
  disabled?: boolean;
  /** Reference medians (hanzi-writer data) drawn faintly as the tracing guide. */
  guide?: Pt[][];
  /** The correct stroke, revealed after 3 errors on the same stroke (green). */
  highlight?: Pt[] | null;
  /** The stroke just drawn wrongly: shown in red at once so the child redraws it. */
  wrong?: Pt[] | null;
  /** Strokes accepted so far (kept on the pad in ink). */
  accepted?: Pt[][];
};

/** Square drawing pad; each finished stroke is reported in the backend's 0..1000 space. */
export default function StrokePad({ onStroke, disabled, guide, highlight, wrong, accepted }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const pts = useRef<{ x: number; y: number }[]>([]);
  const down = useRef(false);

  const line = (g: CanvasRenderingContext2D, p: Pt[], width: number, color: string) => {
    g.lineWidth = width; g.lineCap = "round"; g.lineJoin = "round"; g.strokeStyle = color;
    g.beginPath();
    p.forEach((q, i) => {
      const [x, y] = toCanvas(q, SIZE);
      if (i) g.lineTo(x, y); else g.moveTo(x, y);
    });
    g.stroke();
  };

  const base = useCallback(() => {
    const c = ref.current;
    if (!c) return;
    const g = c.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, SIZE, SIZE);
    for (const s of guide ?? []) line(g, s, 28, "#e3e8e4");
    for (const s of accepted ?? []) line(g, s, 10, "#1d2b22");
    if (highlight) line(g, highlight, 18, "#2e9e5b");
    if (wrong) line(g, wrong, 12, "#e0533d");
  }, [guide, highlight, wrong, accepted]);

  useEffect(() => { base(); }, [base]);

  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return clampPoint(e.clientX - r.left, e.clientY - r.top, r.width);
  };
  const drawCurrent = () => {
    const c = ref.current!;
    const g = c.getContext("2d")!;
    const k = c.width / c.getBoundingClientRect().width;
    g.lineWidth = 8; g.lineCap = "round"; g.strokeStyle = "#1d2b22";
    g.beginPath();
    pts.current.forEach((p, i) => (i ? g.lineTo(p.x * k, p.y * k) : g.moveTo(p.x * k, p.y * k)));
    g.stroke();
  };

  return (
    <canvas
      ref={ref}
      width={SIZE}
      height={SIZE}
      className="pad"
      data-testid="stroke-pad"
      data-wrong={wrong ? "1" : "0"}
      data-answer={highlight ? "1" : "0"}
      aria-label={wrong ? "틀린 획이 빨간색으로 표시됐어요" : "따라쓰기 칸"}
      onPointerDown={(e) => {
        if (disabled) return;
        ref.current!.setPointerCapture(e.pointerId);
        down.current = true;
        pts.current = [pos(e)];
      }}
      onPointerMove={(e) => {
        if (!down.current) return;
        pts.current.push(pos(e));
        drawCurrent();
      }}
      onPointerUp={() => {
        if (!down.current) return;
        down.current = false;
        const size = ref.current!.getBoundingClientRect().width;
        const s = simplify(normalize(pts.current, size));
        pts.current = [];
        base();
        if (s.length >= 2) onStroke(s);
      }}
    />
  );
}
