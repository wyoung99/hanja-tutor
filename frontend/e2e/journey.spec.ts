import { expect, Page, test } from "@playwright/test";
import { addChild, CHILD_PW, childLogin, draw, logout, PARENT_PW, parentLogin, signup, uid, type Pt } from "./helpers";

/** Real hanzi-writer medians for `ch` (same data the backend grades against), via the app's /api proxy. */
async function medians(page: Page, ch: string): Promise<Pt[][]> {
  const r = await page.request.get(`/api/strokes/${encodeURIComponent(ch)}`);
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.mode).toBe("graded");
  return body.strokes;
}

test("installable: manifest + service worker", async ({ page, request }) => {
  await page.goto("/");
  const href = await page.locator('link[rel="manifest"]').getAttribute("href");
  expect(href).toBeTruthy();
  const m = await (await request.get(href!)).json();
  expect(m.name).toBeTruthy();
  expect(m.start_url).toBe("/");
  expect(["standalone", "fullscreen", "minimal-ui"]).toContain(m.display);
  const sizes = m.icons.map((i: any) => i.sizes);
  expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  for (const i of m.icons) {
    const r = await request.get(i.src);
    expect(r.ok()).toBeTruthy();
    expect(r.headers()["content-type"]).toContain("image/png");
  }
  const scope = await page.evaluate(async () => {
    const reg = await navigator.serviceWorker.ready;
    return { active: !!reg.active, scope: reg.scope };
  });
  expect(scope.active).toBe(true);
  // app shell works offline thanks to the service worker
  await page.reload();
  await page.context().setOffline(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "한자 튜터" })).toBeVisible();
  await page.context().setOffline(false);
});

// AC17: 로그인/회원가입 → 자녀 계정 생성 → 자녀 로그인 → 오늘의 한자 → 따라쓰기 → 모의고사 → 챗봇 → (부모) 리포트.
// Subscription payment/refund are implement-only (AC14/AC15 verification excluded) and not part of this E2E.
test("full journey: login page → signup → child account → child login → today → tracing → exam → chatbot → report", async ({ page, browser }) => {
  // landing page = login (one form for parents and children) with a link to the separate sign-up page
  await page.goto("/");
  await expect(page.getByTestId("login-form")).toBeVisible();
  await expect(page.getByTestId("signup-form")).toHaveCount(0);
  await page.getByTestId("signup-link").click();
  await expect(page).toHaveURL(/\/signup$/);
  await expect(page.getByTestId("signup-form")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("본인인증");

  // 회원가입: id + password + email + consents (consent is required; no phone verification)
  const mom = uid("mom");
  await page.getByTestId("signup-id").fill(mom);
  await page.getByTestId("signup-password").fill(PARENT_PW);
  await page.getByTestId("signup-confirm").fill(PARENT_PW);
  await page.getByTestId("email").fill("parent@example.com");
  await page.getByTestId("signup").click();
  await expect(page.getByTestId("signup-error")).toContainText("동의");
  await page.getByTestId("agree-terms").check();
  await page.getByTestId("agree-privacy").check();
  await page.getByTestId("agree-guardian").check();
  await page.getByTestId("signup").click();

  // parent: management menus only
  await expect(page).toHaveURL(/\/$/);
  const parentMenu = page.getByTestId("parent-menu");
  await expect(parentMenu).toBeVisible();
  for (const t of ["tab-kids", "tab-report", "tab-alerts", "tab-billing", "tab-account"]) await expect(page.getByTestId(t)).toBeVisible();
  for (const t of ["tab-today", "tab-review", "tab-tracing", "tab-exam", "tab-chat"]) await expect(page.getByTestId(t)).toHaveCount(0);

  // 자녀 계정 생성 (parent sets id + password + nickname, AI consent, body/grade)
  const kid = uid("star");
  await addChild(page, "별이", kid);
  await expect(page.getByTestId("child-card")).toHaveCount(1);

  // 자녀 로그인 on the child's own device: learning menus only
  const child = await browser.newPage();
  await childLogin(child, kid);
  for (const t of ["tab-today", "tab-review", "tab-tracing", "tab-exam", "tab-chat"]) await expect(child.getByTestId(t)).toBeVisible();
  for (const t of ["tab-kids", "tab-report", "tab-alerts", "tab-billing", "tab-account", "parent-menu"]) await expect(child.getByTestId(t)).toHaveCount(0);

  // 오늘의 한자 (실제 DB 훈·음) + 따라쓰기 (실제 획순 데이터)
  const cards = child.getByTestId("today-card");
  await expect(cards.first()).toBeVisible();
  const n = await cards.count();
  expect(n).toBeGreaterThanOrEqual(3);
  expect(n).toBeLessThanOrEqual(15);
  await expect(child.getByTestId("reading").first()).not.toHaveText(/^[훈음]\d+/);
  const trace = child.locator('[data-testid^="trace-"]').first();
  const ch = (await trace.getAttribute("data-testid"))!.slice("trace-".length);
  const strokes = await medians(child, ch);
  expect(strokes.length).toBeGreaterThan(0);
  await trace.click();
  await expect(child.getByTestId("stroke-pad")).toBeVisible();
  for (const s of strokes) await draw(child, s); // follow each real median in order
  await expect(child.getByTestId("trace-msg")).toContainText("통과");
  await child.getByTestId("trace-finish").click();
  await expect(child.getByTestId(`done-${ch}`)).toHaveCount(1);
  await child.getByTestId("tab-review").click();
  await expect(child.getByLabel("복습")).toBeVisible();
  await child.getByTestId("tab-tracing").click();
  await expect(child.getByTestId("tracing-card").first()).toBeVisible();

  // 모의고사 (실전형): 1회 응시 후 주간 한도 안내(구독 언급 없음)
  await child.getByTestId("tab-exam").click();
  await child.getByTestId("exam-start").click();
  await expect(child.getByTestId("exam-question").first()).toBeVisible();
  await expect(child.getByTestId("exam-question")).toHaveCount(50);
  await child.locator('[data-testid="exam-question"] input[type="radio"]').first().check();
  await child.getByTestId("exam-submit").click();
  await expect(child.getByTestId("exam-score")).toContainText("점");
  await expect(child.getByTestId("exam-pass")).toContainText(/합격|불합격/);
  await child.getByTestId("tab-today").click();
  await child.getByTestId("tab-exam").click();
  await child.getByTestId("exam-start").click();
  await expect(child.getByTestId("exam-msg")).toContainText("또 만나요");
  await expect(child.getByTestId("exam-msg")).not.toContainText("구독");

  // 챗봇
  await child.getByTestId("tab-chat").click();
  await child.getByTestId("chat-input").fill("學이 뭐예요?");
  await child.getByTestId("chat-send").click();
  await expect(child.getByTestId("msg-bot").first()).toContainText("學");
  await child.close();

  // 부모: 리포트 (기본은 무료, 상세는 구독 전용)
  await page.getByTestId("tab-report").click();
  await expect(page.getByTestId("basic-report")).toContainText("학습 시간");
  await page.getByTestId("detail-report").click();
  await expect(page.getByTestId("detail-msg")).toContainText("구독 전용");
  await logout(page);
});

test("준급: learning works and the mock exam uses the official 대한검정회 format", async ({ page }) => {
  const mom = uid("jun");
  await signup(page, mom);
  await page.getByTestId("nick").fill("준이");
  await page.getByTestId("body").selectOption("daegeom");
  await page.getByTestId("grade").selectOption("준5");
  await expect(page.getByTestId("grade-note")).toHaveCount(0);
  await expect(page.getByTestId("grade")).not.toContainText("형식 준비 중");
  await expect(page.getByTestId("grade-format")).toContainText("50문항 · 40분");
  await expect(page.getByTestId("grade-format")).toContainText("문항 기준 35/50문항");
  await expect(page.getByTestId("grade-format")).toContainText("모두 객관식");
  const kid = uid("jun");
  await page.getByTestId("child-login-id").fill(kid);
  await page.getByTestId("child-password").fill(CHILD_PW);
  await page.getByTestId("add-child").click();
  await expect(page.getByTestId("child-created")).toContainText(kid);
  await logout(page);
  await childLogin(page, kid);
  await expect(page.getByTestId("today-card").first()).toBeVisible();
  await page.getByTestId("tab-exam").click();
  await page.getByTestId("exam-start").click();
  await expect(page.getByTestId("exam-question")).toHaveCount(50);
  await expect(page.getByTestId("exam-format")).toContainText("40분");
  await expect(page.getByTestId("exam-format")).toContainText("70/100점 (문항 기준 35/50문항, 1문항 2점)");
  await expect(page.locator('[data-testid="exam-question"][data-type="writing"]')).toHaveCount(0);
  await page.getByTestId("exam-submit").click();
  await expect(page.getByTestId("exam-score")).toContainText("0 / 100점 · 0/50문항 맞힘");
  await expect(page.getByTestId("exam-by-type")).toContainText("훈음 고르기");
  await expect(page.getByTestId("exam-notice")).toHaveCount(0);
  await logout(page);
  await parentLogin(page, mom); // the parent can log in again on the same device
});
