import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api, authFromLogin, authMessage, ApiError, CHILD_MENUS, loadAuth, loginIdError, menusFor, PARENT_MENUS,
  passwordError, session, setAuth, signupError,
} from "./api";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const ok = { loginId: "mom01", password: "parent-pw-1", confirm: "parent-pw-1", email: "a@b.kr", terms: true, privacy: true, guardian: true };

describe("role menus", () => {
  it("a child sees learning menus only", () => {
    expect(menusFor("child").map((m) => m.label)).toEqual(["오늘의 한자", "복습", "따라쓰기", "모의고사", "챗봇"]);
    const parentLabels = PARENT_MENUS.map((m) => m.label);
    for (const m of CHILD_MENUS) expect(parentLabels).not.toContain(m.label);
    for (const word of ["자녀", "리포트", "알림", "구독", "계정", "대화 열람", "사용 시간"])
      expect(menusFor("child").some((m) => m.label.includes(word))).toBe(false);
  });
  it("a parent sees management menus only", () => {
    expect(menusFor("parent").map((m) => m.key)).toEqual(["kids", "report", "alerts", "billing", "account"]);
    for (const m of ["today", "review", "tracing", "exam", "chat"])
      expect(menusFor("parent").some((x) => x.key === m)).toBe(false);
  });
});

describe("sign-up / password rules (same as the server)", () => {
  it("accepts a valid parent sign-up without any verification field", () => {
    expect(signupError(ok)).toBe("");
    expect(Object.keys(ok)).not.toContain("verification_token");
  });
  it("rejects bad ids, weak passwords, mismatched confirmation, bad email and missing consents", () => {
    expect(signupError({ ...ok, loginId: "ab" })).toMatch(/아이디/);
    expect(signupError({ ...ok, password: "short1", confirm: "short1" })).toMatch(/8자/);
    expect(signupError({ ...ok, password: "onlyletters", confirm: "onlyletters" })).toMatch(/8자/);
    expect(signupError({ ...ok, confirm: "other-pw-1" })).toMatch(/확인/);
    expect(signupError({ ...ok, email: "nope" })).toMatch(/이메일/);
    expect(signupError({ ...ok, guardian: false })).toMatch(/동의/);
  });
  it("child passwords need 4+ characters, ids 4-20 [A-Za-z0-9._-]", () => {
    expect(passwordError("abc", "child")).not.toBe("");
    expect(passwordError("1234", "child")).toBe("");
    expect(loginIdError("kid-01")).toBe("");
    expect(loginIdError("아이디")).not.toBe("");
  });
  it("explains login errors in Korean (remaining attempts, lock)", () => {
    expect(authMessage(new ApiError(401, { code: "invalid_credentials", remaining: 2 }))).toContain("2번");
    expect(authMessage(new ApiError(423, { code: "profile_locked" }))).toContain("부모님");
    expect(authMessage(new ApiError(409, { code: "login_id_taken" }))).toContain("이미");
  });
});

describe("session", () => {
  afterEach(() => { setAuth(null); vi.unstubAllGlobals(); });
  it("every API call carries the logged-in session, and a revoked one logs out", async () => {
    const a = authFromLogin({ session: "tok-1", role: "child", account_id: "c1", parent_id: "p1", nickname: "별이" });
    setAuth(a);
    expect(session.token).toBe("tok-1");
    const calls: any[] = [];
    vi.stubGlobal("fetch", async (url: string, init: any) => {
      calls.push([url, init.headers]);
      return new Response(JSON.stringify({ detail: { code: "login_required" } }), { status: 401 });
    });
    await expect(api("/children/c1/today")).rejects.toBeInstanceOf(ApiError);
    expect(calls[0][1].Authorization).toBe("Bearer tok-1");
    expect(session.token).toBeNull(); // 401 on a session -> back to the login screen
  });
  it("persists the login in localStorage", () => {
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; }, removeItem: (k: string) => { delete store[k]; } });
    setAuth(authFromLogin({ session: "tok-2", role: "parent", account_id: "p1", parent_id: "p1" }));
    session.token = null;
    expect(loadAuth()?.role).toBe("parent");
    expect(session.token).toBe("tok-2");
    setAuth(null);
    expect(loadAuth()).toBeNull();
  });
});

describe("routes", () => {
  it("landing page is the login screen and sign-up is a separate page", () => {
    const home = readFileSync(resolve(__dirname, "../app/page.tsx"), "utf-8");
    expect(home).toContain("<LoginForm");
    expect(existsSync(resolve(__dirname, "../app/signup/page.tsx"))).toBe(true);
    const auth = readFileSync(resolve(__dirname, "../components/Auth.tsx"), "utf-8");
    expect(auth).toContain('href="/signup"');
    expect(auth + home).not.toMatch(/dev\/verify|본인인증|verification_token/);
  });
});
