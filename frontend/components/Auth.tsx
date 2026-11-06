"use client";
import { useState } from "react";
import { api, authFromLogin, authMessage, setAuth, signupError, type Auth } from "../lib/api";
import { Alert, Button, Card, Check, Field, inputClass, Muted } from "./ui";

/** One login form for parents and children: the server decides the role from the id. */
export function LoginForm({ onLoggedIn }: { onLoggedIn: (a: Auth) => void }) {
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const a = authFromLogin(await api("/auth/login", { body: { login_id: loginId.trim(), password } }));
      setAuth(a);
      setPassword("");
      onLoggedIn(a);
    } catch (x) {
      setErr(authMessage(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card as="form" aria-label="로그인" data-testid="login-form" onSubmit={submit} className="space-y-4">
      <div>
        <h2 className="text-xl font-bold">로그인</h2>
        <Muted className="mt-1">부모님과 어린이 모두 여기에서 아이디와 비밀번호로 로그인해요.</Muted>
      </div>
      <Field label="아이디"><input className={inputClass} data-testid="login-id" autoComplete="username" value={loginId} onChange={(e) => setLoginId(e.target.value)} /></Field>
      <Field label="비밀번호"><input className={inputClass} data-testid="login-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
      {err && <Alert tone="danger" role="alert" data-testid="login-error">{err}</Alert>}
      <Button className="w-full" size="lg" data-testid="login" type="submit" disabled={busy || !loginId || !password}>로그인</Button>
      <div className="border-t border-line pt-4 text-center text-sm">
        <p>처음이신가요? <a href="/signup" data-testid="signup-link">회원가입 (학부모)</a></p>
        <p className="mt-1 text-muted"><small>어린이 계정은 부모님이 로그인한 뒤 &lsquo;자녀 관리&rsquo;에서 만들어요.</small></p>
      </div>
    </Card>
  );
}

/** Parent sign-up: id, password (+ confirm), email, legal consents. No phone verification. */
export function SignupForm({ onSignedUp }: { onSignedUp: (a: Auth) => void }) {
  const [f, setF] = useState({ loginId: "", password: "", confirm: "", email: "", terms: false, privacy: false, guardian: false });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const local = signupError(f);
    if (local) return setErr(local);
    setErr("");
    setBusy(true);
    try {
      await api("/parents/signup", {
        body: { login_id: f.loginId.trim(), password: f.password, email: f.email.trim(), agree_terms: f.terms, agree_privacy: f.privacy, agree_legal_guardian: f.guardian },
      });
      const a = authFromLogin(await api("/auth/login", { body: { login_id: f.loginId.trim(), password: f.password } }));
      setAuth(a);
      onSignedUp(a);
    } catch (x) {
      setErr(authMessage(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card as="form" aria-label="회원가입" data-testid="signup-form" onSubmit={submit} className="space-y-4">
      <h2 className="text-xl font-bold">학부모 회원가입</h2>
      <Field label="아이디" hint="(영문·숫자 4~20자)"><input className={inputClass} data-testid="signup-id" autoComplete="username" value={f.loginId} onChange={set("loginId")} /></Field>
      <Field label="비밀번호" hint="(영문+숫자 8자 이상)"><input className={inputClass} data-testid="signup-password" type="password" autoComplete="new-password" value={f.password} onChange={set("password")} /></Field>
      <Field label="비밀번호 확인"><input className={inputClass} data-testid="signup-confirm" type="password" autoComplete="new-password" value={f.confirm} onChange={set("confirm")} /></Field>
      <Field label="이메일" hint="(결제 실패 알림용)"><input className={inputClass} data-testid="email" type="email" autoComplete="email" value={f.email} onChange={set("email")} /></Field>
      <fieldset className="rounded-xl border border-line bg-soft/60 px-4 py-2">
        <legend className="sr-only">동의 항목</legend>
        <Check data-testid="agree-terms" checked={f.terms} onChange={set("terms")}>이용약관 동의</Check>
        <Check data-testid="agree-privacy" checked={f.privacy} onChange={set("privacy")}>개인정보 수집·이용 동의</Check>
        <Check data-testid="agree-guardian" checked={f.guardian} onChange={set("guardian")}>저는 법정대리인입니다</Check>
      </fieldset>
      {err && <Alert tone="danger" role="alert" data-testid="signup-error">{err}</Alert>}
      <Button className="w-full" size="lg" data-testid="signup" type="submit" disabled={busy}>가입하기</Button>
      <p className="border-t border-line pt-4 text-center text-sm">이미 계정이 있나요? <a href="/" data-testid="login-link">로그인</a></p>
    </Card>
  );
}
