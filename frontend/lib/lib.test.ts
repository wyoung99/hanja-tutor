import { describe, expect, it } from "vitest";
import { childLockMessage, passLine, scoreLine, TYPE_LABEL, won } from "./api";
import { manifestProblems } from "./pwa";
import { gradeLabel, normalize, simplify, toCanvas } from "./strokes";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const manifest = JSON.parse(readFileSync(resolve(__dirname, "../public/manifest.webmanifest"), "utf-8"));

describe("strokes", () => {
  it("normalizes pad pixels into the 0..1000 board", () => {
    expect(normalize([{ x: 0, y: 150 }, { x: 300, y: 300 }], 300)).toEqual([[0, 500], [1000, 1000]]);
  });
  it("maps board points onto the canvas (inverse of normalize)", () => {
    expect(toCanvas([500, 250], 600)).toEqual([300, 150]);
    expect(normalize([{ x: 300, y: 150 }], 600)).toEqual([[500, 250]]);
  });
  it("labels grades including 준급", () => {
    expect(["8", "7II", "준5", "준3"].map(gradeLabel)).toEqual(["8급", "7Ⅱ급", "준5급", "준3급"]);
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

describe("exam scoring copy", () => {
  it("대한검정회: points with the official question pass line", () => {
    expect(passLine({ pass_score: 70, total: 100, points_per_question: 4, pass_questions: 18, question_count: 25 }))
      .toBe("70/100점 (문항 기준 18/25문항, 1문항 4점)");
    expect(passLine({ pass_score: 70, total_score: 100, points_per_question: 2, pass_questions: 35, questions: 50 }))
      .toBe("70/100점 (문항 기준 35/50문항, 1문항 2점)");
    expect(scoreLine({ score: 72, total: 100, pass_score: 70, points_per_question: 4, correct_count: 18, question_count: 25 }))
      .toBe("72 / 100점 · 18/25문항 맞힘");
  });
  it("한국어문회: 1 point per question, no duplicate question line", () => {
    expect(passLine({ pass_score: 35, total: 50, points_per_question: 1, pass_questions: 35, question_count: 50 })).toBe("35/50점");
    expect(scoreLine({ score: 40, total: 50, pass_score: 35, points_per_question: 1, correct_count: 40, question_count: 50 })).toBe("40 / 50점");
  });
  it("labels every app question type", () => {
    for (const t of ["eum", "hun_eum", "hun_to_hanja", "writing", "busu", "pilsun"]) expect(TYPE_LABEL[t]).toBeTruthy();
  });
});

describe("child copy", () => {
  it("never mentions subscriptions to children", () => {
    for (const f of ["chatbot", "mockexam"] as const) expect(childLockMessage(f)).not.toMatch(/구독|결제|유료/);
  });
  it("formats won", () => expect(won(5900)).toBe("5,900원"));
});

import { mmss, parseSse, pct } from "./api";

describe("sse", () => {
  it("parses complete frames and keeps the tail", () => {
    const { events, rest } = parseSse('event: thread\ndata: {"type":"thread","thread_id":"a"}\n\nevent: token\ndata: {"type":"token","text":"學"}\n\nevent: do');
    expect(events.map((e) => e.type)).toEqual(["thread", "token"]);
    expect(events[1].text).toBe("學");
    expect(rest).toBe("event: do");
  });
  it("handles CRLF and ignores malformed data", () => {
    expect(parseSse('data: {"type":"done"}\r\n\r\ndata: {oops\n\n').events).toEqual([{ type: "done" }]);
  });
});

describe("exam helpers", () => {
  it("formats the countdown", () => {
    expect(mmss(3000)).toBe("50:00");
    expect(mmss(61.9)).toBe("01:01");
    expect(mmss(-5)).toBe("00:00");
  });
  it("formats rates", () => {
    expect(pct(0.456)).toBe("46%");
    expect(pct(null)).toBe("-");
  });
});

import { afterEach, vi } from "vitest";
import { ApiError, streamChat } from "./api";

describe("streamChat", () => {
  afterEach(() => vi.unstubAllGlobals());
  const sseBody = (frames: string[]) => new Response(new ReadableStream({
    start(c) { for (const f of frames) c.enqueue(new TextEncoder().encode(f)); c.close(); },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });

  it("delivers tokens then done", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sseBody(['data: {"type":"token","text":"가"}\n\n', 'data: {"type":"done","reply":"가나"}\n\n'])));
    const seen: string[] = [];
    expect(await streamChat("/x", {}, (e) => seen.push(e.type))).toBe("done");
    expect(seen).toEqual(["token", "done"]);
  });
  it("reports a server error event as error (retry button)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sseBody(['data: {"type":"error","retry":true}\n\n'])));
    expect(await streamChat("/x", {}, () => undefined)).toBe("error");
  });
  it("times out when the answer does not finish in time", async () => {
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise((_, rej) => {
      init.signal!.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    })));
    expect(await streamChat("/x", {}, () => undefined, 20)).toBe("timeout");
  });
  it("throws ApiError for HTTP errors before streaming", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { code: "chatbot_disabled_no_ai_consent" } }), { status: 403 })));
    await expect(streamChat("/x", {}, () => undefined)).rejects.toBeInstanceOf(ApiError);
  });
});
