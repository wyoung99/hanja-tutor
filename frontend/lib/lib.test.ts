import { describe, expect, it } from "vitest";
import { childLockMessage, won } from "./api";
import { manifestProblems } from "./pwa";
import { normalize, simplify } from "./strokes";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const manifest = JSON.parse(readFileSync(resolve(__dirname, "../public/manifest.webmanifest"), "utf-8"));

describe("strokes", () => {
  it("normalizes pad pixels into the 0..1000 board", () => {
    expect(normalize([{ x: 0, y: 150 }, { x: 300, y: 300 }], 300)).toEqual([[0, 500], [1000, 1000]]);
  });
  it("simplify keeps endpoints and drops near-duplicates", () => {
    const out = simplify([[0, 0], [1, 1], [2, 2], [500, 500], [501, 501]]);
    expect(out[0]).toEqual([0, 0]);
    expect(out[out.length - 1]).toEqual([501, 501]);
    expect(out.length).toBeLessThan(5);
  });
});

describe("pwa manifest", () => {
  it("is installable", () => expect(manifestProblems(manifest as any)).toEqual([]));
  it("flags missing icons", () => expect(manifestProblems({ name: "x", start_url: "/", display: "standalone", icons: [] })).toEqual(["icon192", "icon512"]));
});

describe("child copy", () => {
  it("never mentions subscriptions to children", () => {
    for (const f of ["chatbot", "mockexam"] as const) expect(childLockMessage(f)).not.toMatch(/구독|결제|유료/);
  });
  it("formats won", () => expect(won(5900)).toBe("5,900원"));
});
