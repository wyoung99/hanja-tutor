export type Pt = [number, number];

export const BOARD = 1000; // backend stroke coordinate space (0..1000)

/** Convert client pixel points on a square pad into the backend's 0..1000 space. */
export function normalize(points: { x: number; y: number }[], size: number): Pt[] {
  const s = size > 0 ? size : 1;
  return points.map((p) => [Math.round((p.x / s) * BOARD), Math.round((p.y / s) * BOARD)] as Pt);
}

export function clampPoint(x: number, y: number, size: number) {
  return { x: Math.min(Math.max(x, 0), size), y: Math.min(Math.max(y, 0), size) };
}

/** Drop near-duplicate points so payloads stay small. */
export function simplify(points: Pt[], minDist = 4): Pt[] {
  const out: Pt[] = [];
  for (const p of points) {
    const q = out[out.length - 1];
    if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) >= minDist) out.push(p);
  }
  const last = points[points.length - 1];
  if (last && out[out.length - 1] !== last) out.push(last);
  return out;
}
