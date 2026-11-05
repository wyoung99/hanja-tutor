import { expect, Page, test } from "@playwright/test";

// Feature E2E for AC2-AC11 against the local stack (uvicorn + next start, see playwright.config.ts).
// Time-dependent flows use the non-production POST /api/dev/clock shift and always reset it.

type Pt = [number, number];
let seq = 0;
const uniq = (p: string) => `${p}${Date.now()}${seq++}@example.com`;

async function draw(page: Page, pts: Pt[]) {
  const box = (await page.getByTestId("stroke-pad").first().boundingBox())!;
  const at = ([x, y]: Pt) => [box.x + (x / 1000) * box.width, box.y + (y / 1000) * box.height] as const;
  await page.mouse.move(...at(pts[0]));
  await page.mouse.down();
  for (const p of pts.slice(1)) await page.mouse.move(...at(p), { steps: 6 });
  await page.mouse.up();
}

async function signup(page: Page, email: string) {
  await page.goto("/");
  await page.getByTestId("email").fill(email);
  for (const t of ["agree-terms", "agree-privacy", "agree-guardian"]) await page.getByTestId(t).check();
  await page.getByTestId("signup").click();
  await expect(page.getByTestId("nick")).toBeVisible();
}

async function login(page: Page, email: string) {
  await page.goto("/");
  await page.getByTestId("email").fill(email);
  await page.getByTestId("login").click();
  await expect(page.getByTestId("nick")).toBeVisible();
}

async function addChild(page: Page, nick: string, opts: { consent?: boolean; body?: string; grade?: string } = {}) {
  await page.getByTestId("nick").fill(nick);
  await page.getByTestId("pin").fill("1234");
  await page.getByTestId("body").selectOption(opts.body ?? "korea_eomunhoe");
  await page.getByTestId("grade").selectOption(opts.grade ?? "8");
  if (opts.consent ?? true) await page.getByTestId("ai-consent").check();
  await page.getByTestId("add-child").click();
  await expect(page.getByTestId(`enter-${nick}`)).toBeVisible();
}

async function enter(page: Page, nick: string, pin = "1234"): Promise<string> {
  await page.getByTestId(`enter-${nick}`).click();
  await page.getByTestId("child-pin").fill(pin);
  const hb = page.waitForResponse((r) => r.url().includes("/heartbeat"));
  await page.getByTestId("child-unlock").click();
  const url = (await hb).url();
  return url.split("/children/")[1].split("/")[0]; // child id
}

async function shiftClock(page: Page, minutes: number) {
  const r = await page.request.post("/api/dev/clock", { data: { advance_minutes: minutes } });
  expect(r.ok()).toBeTruthy();
}
const resetClock = (page: Page) => page.request.post("/api/dev/clock", { data: { reset: true } });

test("AC2: no AI consent disables only the chatbot; 5 wrong PINs lock; parent reset unlocks", async ({ page }) => {
  await signup(page, uniq("consent"));
  await addChild(page, "솔이", { consent: false });
  await enter(page, "솔이");
  await expect(page.getByTestId("today-card").first()).toBeVisible(); // learning still works
  await page.getByTestId("tab-chat").click();
  await expect(page.getByTestId("chat-disabled")).toBeVisible();
  await page.getByRole("button", { name: "나가기" }).click();

  await page.getByTestId("enter-솔이").click();
  for (let i = 0; i < 4; i++) {
    await page.getByTestId("child-pin").fill("0000");
    await page.getByTestId("child-unlock").click();
    await expect(page.getByTestId("pin-error")).toContainText(`${4 - i}번 남았어요`);
  }
  await page.getByTestId("child-pin").fill("0000");
  await page.getByTestId("child-unlock").click();
  await expect(page.getByTestId("pin-error")).toContainText("잠겼어요");
  await page.getByTestId("child-pin").fill("1234");
  await page.getByTestId("child-unlock").click();
  await expect(page.getByTestId("pin-error")).toContainText("잠겼어요"); // even the right PIN
  await page.getByRole("button", { name: "뒤로" }).click();

  await page.getByTestId("tab-kids").click();
  await expect(page.getByTestId("locked-솔이")).toBeVisible();
  await page.getByTestId("pin-솔이").click();
  await page.getByTestId("newpin-솔이").fill("5678");
  await page.getByTestId("resetpin-솔이").click();
  await expect(page.getByTestId("child-msg-솔이")).toContainText("잠금이 풀렸어요");
  await expect(page.getByTestId("locked-솔이")).toHaveCount(0);
  await page.getByTestId("consent-솔이").click(); // controlled checkbox: state follows the API response
  await expect(page.getByTestId("consent-솔이")).toBeChecked();

  await enter(page, "솔이", "5678");
  await page.getByTestId("tab-chat").click();
  await page.getByTestId("chat-input").fill("學이 뭐예요?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("msg-bot").last()).toContainText("學");
});

test("AC4: parent edits the daily count and the child's today list follows", async ({ page }) => {
  await signup(page, uniq("plan"));
  await addChild(page, "계획이", { grade: "7" });
  await page.getByTestId("plan-계획이").click();
  await page.getByTestId("plan-count").fill("5");
  await page.getByTestId("plan-save").click();
  await expect(page.getByTestId("plan-summary")).toContainText("오늘 새 한자 5자");
  await enter(page, "계획이");
  await expect(page.getByTestId("today-card")).toHaveCount(5);
});

test("AC6: leaving resumes the exam with the timer running; time-out auto-submits with per-type results", async ({ page }) => {
  const email = uniq("exam");
  await signup(page, email);
  await addChild(page, "시험이");
  await enter(page, "시험이");
  await page.getByTestId("tab-exam").click();
  await page.getByTestId("exam-start").click();
  await expect(page.getByTestId("exam-question")).toHaveCount(50);
  await expect(page.getByTestId("stroke-figure")).toHaveCount(2); // 한국어문회 8급 筆順 2문항
  const examId = await page.getByTestId("exam-running").getAttribute("data-exam-id");
  await expect(page.getByTestId("exam-timer")).toContainText(/남은 시간 (50:00|49:5\d)/);
  await page.locator('[data-testid="exam-question"]').first().locator('input[type="radio"]').first().check();

  // leave inside the app and come back
  await page.getByTestId("tab-today").click();
  await page.getByTestId("tab-exam").click();
  await expect(page.getByTestId("exam-running")).toHaveAttribute("data-exam-id", examId!);
  await expect(page.locator('[data-testid="exam-question"]').first().locator('input[type="radio"]:checked')).toHaveCount(1);

  // close the app entirely (reload), log in again: same exam, timer kept running
  await page.reload();
  await login(page, email);
  await enter(page, "시험이");
  await page.getByTestId("tab-exam").click();
  await expect(page.getByTestId("exam-running")).toHaveAttribute("data-exam-id", examId!);
  try {
    await shiftClock(page, 30);
    await expect(page.getByTestId("exam-timer")).toContainText(/남은 시간 (19|20):\d\d/, { timeout: 12_000 });
    await shiftClock(page, 21); // past the 50-minute limit
    await expect(page.getByTestId("exam-result")).toBeVisible({ timeout: 15_000 });
  } finally {
    await resetClock(page);
  }
  await expect(page.getByTestId("exam-auto")).toBeVisible();
  await expect(page.getByTestId("exam-by-type")).toContainText("讀音");
  await expect(page.getByTestId("exam-by-type")).toContainText("訓音");
  await expect(page.getByTestId("exam-by-type")).toContainText("筆順");
  await expect(page.getByTestId("exam-notice")).toContainText("비슷한 유형으로 대체");
  await expect(page.getByTestId("exam-wrong").locator("li").first()).toContainText("훈음은");
  // no second deduction: the weekly start is used up, resume/auto-submit did not charge again
  await page.getByRole("button", { name: "닫기" }).click();
  await page.getByTestId("exam-start").click();
  await expect(page.getByTestId("exam-msg")).toContainText("또 만나요");
});

test("AC7: a wrong stroke turns red at once; the 3rd error shows the answer stroke; result is retry", async ({ page }) => {
  await signup(page, uniq("stroke"));
  await addChild(page, "획이");
  await enter(page, "획이");
  const trace = page.locator('[data-testid^="trace-"]').first();
  const ch = (await trace.getAttribute("data-testid"))!.slice("trace-".length);
  const strokes: Pt[][] = (await (await page.request.get(`/api/strokes/${encodeURIComponent(ch)}`)).json()).strokes;
  await trace.click();
  const pad = page.getByTestId("stroke-pad");
  const reversed = [...strokes[0]].reverse() as Pt[];
  await draw(page, reversed);
  await expect(pad).toHaveAttribute("data-wrong", "1");
  await expect(page.getByTestId("trace-msg")).toContainText("빨간 획이 틀렸어요");
  await expect(pad).toHaveAttribute("data-answer", "0");
  await draw(page, reversed);
  await draw(page, reversed);
  await expect(pad).toHaveAttribute("data-answer", "1");
  await expect(page.getByTestId("trace-msg")).toContainText("초록 획이 정답");
  for (const s of strokes) await draw(page, s);
  await expect(page.getByTestId("trace-result")).toHaveText("재시도");
  await expect(pad).toHaveAttribute("data-wrong", "0");
});

test("AC9/AC10/AC12: streamed answers, retry after error and 15s timeout, resume a thread, parent view and delete", async ({ page }) => {
  const email = uniq("chat");
  await signup(page, email);
  await addChild(page, "챗이");
  const cid = await enter(page, "챗이");
  await page.getByTestId("tab-chat").click();
  await page.getByTestId("chat-input").fill("學이 뭐예요?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("msg-bot").last()).toContainText("學");
  await expect(page.getByTestId("thread-item")).toHaveCount(1);

  // server error event -> notice + retry button (server-side release of failed answers: backend pytest)
  await page.route("**/chat/stream", (r) => r.fulfill({ status: 200, contentType: "text/event-stream",
    body: 'event: error\ndata: {"type":"error","retry":true,"status":"error"}\n\n' }));
  await page.getByTestId("chat-input").fill("校는 뭐예요?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-failed")).toContainText("다시 시도");
  await page.unroute("**/chat/stream");
  await page.getByTestId("chat-retry").click();
  await expect(page.getByTestId("chat-failed")).toHaveCount(0);
  await expect(page.getByTestId("msg-bot").last()).toContainText("學");

  // no answer within 15 s -> timeout notice + retry
  await page.route("**/chat/stream", () => undefined); // never answers
  await page.getByTestId("chat-input").fill("山은 뭐예요?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-failed")).toContainText("너무 오래", { timeout: 20_000 });
  await page.unroute("**/chat/stream");
  await page.getByTestId("chat-retry").click();
  await expect(page.getByTestId("chat-failed")).toHaveCount(0);
  await expect(page.getByTestId("msg-bot").nth(2)).toContainText("學"); // the retried answer arrived (not just started)
  const used = (await (await page.request.get(`/api/children/${cid}/usage`)).json()).chatbot.used;
  expect(used).toBe(3); // only the 3 answered questions count
  await page.getByTestId("chat-input").fill("씨발");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("msg-bot").last()).toContainText("다룰 수 없어요");

  // reopen the app (reload + login, like another device) and continue the earlier thread
  await page.reload();
  await login(page, email);
  await enter(page, "챗이");
  await page.getByTestId("tab-chat").click();
  await expect(page.getByTestId("thread-item")).toHaveCount(1);
  await page.getByTestId("thread-item").click();
  await expect(page.getByTestId("msg-user").first()).toHaveText("學이 뭐예요?");
  await page.getByTestId("chat-input").fill("學校는요?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("msg-user")).toHaveCount(4); // 3 stored turns (the blocked input is never stored) + 1
  await expect(page.getByTestId("thread-item")).toHaveCount(1);

  // parent: conversation viewing + safety events (free), then delete -> gone everywhere
  await page.getByRole("button", { name: "나가기" }).click();
  await page.getByTestId("tab-report").click();
  await page.getByTestId("pc-thread").click();
  await expect(page.getByTestId("pc-messages")).toContainText("學이 뭐예요?");
  await expect(page.getByTestId("safety-event")).toHaveCount(1);
  await expect(page.getByTestId("safety-events")).not.toContainText("씨발");
  await page.getByTestId("pc-delete").first().click();
  await expect(page.getByTestId("pc-thread")).toHaveCount(0);
  await page.getByTestId("tab-kids").click();
  await enter(page, "챗이");
  await page.getByTestId("tab-chat").click();
  await expect(page.getByTestId("thread-item")).toHaveCount(0);
});

test("AC11: daily limit -> 5-minute notice -> lock; extension request approved by parent; parent PIN adds time", async ({ page, browser }) => {
  const email = uniq("time");
  await signup(page, email);
  await page.getByTestId("tab-alerts").click();
  await page.getByTestId("parent-pin").fill("9999");
  await page.getByTestId("parent-pin-save").click();
  await expect(page.getByTestId("parent-pin-msg")).toContainText("저장");
  await page.getByTestId("tab-kids").click();
  await addChild(page, "시간이");
  await page.getByTestId("time-시간이").click();
  await page.getByTestId("st-daily").fill("10");
  await page.getByTestId("st-save").click();
  await expect(page.getByTestId("st-msg")).toContainText("저장");
  const cid = await enter(page, "시간이");
  const beat = () => page.request.post(`/api/children/${cid}/heartbeat`);
  try {
    for (let i = 0; i < 3; i++) { await shiftClock(page, 2); await beat(); } // 6 of 10 minutes used
    await expect(page.getByTestId("time-warning")).toBeVisible({ timeout: 20_000 });
    for (let i = 0; i < 2; i++) { await shiftClock(page, 2); await beat(); } // 10 of 10
    await expect(page.getByTestId("app-locked")).toBeVisible({ timeout: 20_000 });

    // child asks for more time -> parent (another device) approves
    await page.getByTestId("ask-extension").click();
    await expect(page.getByTestId("lock-msg")).toContainText("물어봤어요");
    const parent = await browser.newPage();
    await login(parent, email);
    await parent.getByTestId("tab-alerts").click();
    await expect(parent.getByTestId("note-extension_request")).toBeVisible();
    await parent.getByTestId("ext-approve").click();
    await expect(parent.getByTestId("ext-request")).toHaveCount(0);
    await expect(page.getByTestId("app-locked")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByTestId("tab-today")).toBeVisible();

    // use up the extra 15 minutes, then the parent types their PIN on the child's device
    for (let i = 0; i < 8; i++) { await shiftClock(page, 2); await beat(); }
    await expect(page.getByTestId("app-locked")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("parent-pin-extra").fill("0000");
    await page.getByTestId("grant-extra").click();
    await expect(page.getByTestId("lock-msg")).toContainText("PIN이 달라요");
    await page.getByTestId("parent-pin-extra").fill("9999");
    await page.getByTestId("grant-extra").click();
    await expect(page.getByTestId("app-locked")).toHaveCount(0, { timeout: 10_000 });
    await parent.close();
  } finally {
    await resetClock(page);
  }
});

test("AC3: deleting a child profile and the account removes them", async ({ page }) => {
  page.on("dialog", (d) => d.accept());
  const email = uniq("del");
  await signup(page, email);
  await addChild(page, "지울이");
  await addChild(page, "남을이");
  await page.getByTestId("delete-지울이").click();
  await expect(page.getByTestId("enter-지울이")).toHaveCount(0);
  await expect(page.getByTestId("enter-남을이")).toBeVisible();
  await page.getByTestId("tab-account").click();
  await page.getByTestId("delete-account").click();
  await expect(page.getByTestId("signup")).toBeVisible();
  await page.getByTestId("email").fill(email);
  await page.getByTestId("login").click();
  await expect(page.locator("p.err")).toContainText("login_failed");
});

test("AC8/AC11/AC12/AC13: parent turns on Web Push (VAPID key from the API) and gets a test alert", async ({ page, context }) => {
  const email = uniq("push");
  await context.grantPermissions(["notifications"], { origin: "http://127.0.0.1:3100" });
  await signup(page, email);
  await page.getByTestId("tab-alerts").click();
  const state = page.getByTestId("push-state");
  await expect(state).not.toHaveAttribute("data-state", "loading");
  // server has VAPID keys and the browser is not subscribed yet ("denied": headless shells refuse notifications)
  await expect(state).toHaveAttribute("data-state", /^(off|denied)$/);
  const cfg = await (await page.request.get("/api/push/config")).json();
  expect(cfg.enabled).toBe(true);
  expect(Buffer.from(cfg.public_key, "base64url").length).toBe(65);

  // subscribe: Chromium either reaches its push service (subscription registered with the backend) or, without
  // one (offline/headless builds), reports an error/denial -- all handled; the inbox/email fallback stays.
  const registered = page.waitForResponse((r) => r.url().endsWith("/api/parents/me/push-subscriptions") && r.request().method() === "POST", { timeout: 20_000 }).catch(() => null);
  await page.getByTestId("push-on").click();
  await expect(state).toHaveAttribute("data-state", /^(on|error|denied)$/, { timeout: 20_000 });
  const reached = (await state.getAttribute("data-state"))!;
  test.info().annotations.push({ type: "browser-push-state", description: reached }); // recorded in e2e-results.json
  if (reached === "on") expect((await registered)!.status()).toBe(201);

  await page.getByTestId("push-test").click();
  await expect(page.getByTestId("push-msg")).toContainText(/테스트 알림을 보냈어요|이메일로 보냈어요/);
  await expect(page.getByTestId("note-push_test")).toBeVisible({ timeout: 10_000 });
  expect(await (await page.request.get("/sw.js")).text()).toContain('addEventListener("push"');
});
