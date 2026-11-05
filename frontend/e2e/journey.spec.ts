import { expect, Page, test } from "@playwright/test";

type Pt = [number, number];

async function draw(page: Page, pts: Pt[]) {
  const box = (await page.getByTestId("stroke-pad").boundingBox())!;
  const at = ([x, y]: Pt) => [box.x + (x / 1000) * box.width, box.y + (y / 1000) * box.height] as const;
  await page.mouse.move(...at(pts[0]));
  await page.mouse.down();
  for (const p of pts.slice(1)) await page.mouse.move(...at(p), { steps: 6 });
  await page.mouse.up();
}

/** Real hanzi-writer medians for `ch` (same data the backend grades against), via the app's /api proxy. */
async function medians(page: Page, ch: string): Promise<Pt[][]> {
  const r = await page.request.get(`/api/strokes/${encodeURIComponent(ch)}`);
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.mode).toBe("graded");
  return body.strokes;
}

async function signup(page: Page, email: string) {
  await page.goto("/");
  await page.getByTestId("email").fill(email);
  for (const t of ["agree-terms", "agree-privacy", "agree-guardian"]) await page.getByTestId(t).check();
  await page.getByTestId("signup").click();
  await expect(page.getByTestId("nick")).toBeVisible();
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

// AC17: 가입·동의 → 자녀 프로필 → 오늘의 한자 → 따라쓰기 → 모의고사 → 챗봇 → 리포트.
// Subscription payment/refund are implement-only (AC14/AC15 verification excluded) and not part of this E2E.
test("full journey: signup → child → today → tracing → mock exam → chatbot → report", async ({ page }) => {
  await page.goto("/");

  // 가입·동의 (consent is required)
  await page.getByTestId("email").fill("parent@example.com");
  await page.getByTestId("signup").click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByTestId("agree-terms").check();
  await page.getByTestId("agree-privacy").check();
  await page.getByTestId("agree-guardian").check();
  await page.getByTestId("signup").click();

  // 자녀 프로필 (닉네임 + PIN, AI 처리 동의, 기관·급수 선택)
  await page.getByTestId("nick").fill("별이");
  await page.getByTestId("pin").fill("1234");
  await page.getByTestId("body").selectOption("korea_eomunhoe");
  await page.getByTestId("grade").selectOption("8");
  await page.getByTestId("ai-consent").check();
  await page.getByTestId("add-child").click();
  await expect(page.getByTestId("child-card")).toHaveCount(1);

  // 아이 모드: 오늘의 한자 (실제 DB 훈·음) + 따라쓰기 (실제 획순 데이터)
  await page.getByTestId("enter-별이").click();
  await page.getByTestId("child-pin").fill("1234");
  await page.getByTestId("child-unlock").click();
  const cards = page.getByTestId("today-card");
  await expect(cards.first()).toBeVisible();
  const n = await cards.count();
  expect(n).toBeGreaterThanOrEqual(3);
  expect(n).toBeLessThanOrEqual(15);
  await expect(page.getByTestId("reading").first()).not.toHaveText(/^[훈음]\d+/);
  const trace = page.locator('[data-testid^="trace-"]').first();
  const ch = (await trace.getAttribute("data-testid"))!.slice("trace-".length);
  const strokes = await medians(page, ch);
  expect(strokes.length).toBeGreaterThan(0);
  await trace.click();
  await expect(page.getByTestId("stroke-pad")).toBeVisible();
  for (const s of strokes) await draw(page, s); // follow each real median in order
  await expect(page.getByTestId("trace-msg")).toContainText("통과");
  await page.getByTestId("trace-finish").click();
  await expect(page.getByTestId(`done-${ch}`)).toHaveCount(1);

  // 모의고사 (실전형): 1회 응시 후 주간 한도 안내(구독 언급 없음)
  await page.getByTestId("tab-exam").click();
  await page.getByTestId("exam-start").click();
  await expect(page.getByTestId("exam-question").first()).toBeVisible();
  await expect(page.getByTestId("exam-question")).toHaveCount(50);
  await page.locator('[data-testid="exam-question"] input[type="radio"]').first().check();
  await page.getByTestId("exam-submit").click();
  await expect(page.getByTestId("exam-score")).toContainText("점");
  await expect(page.getByTestId("exam-pass")).toContainText(/합격|불합격/);
  await page.getByTestId("tab-today").click();
  await page.getByTestId("tab-exam").click();
  await page.getByTestId("exam-start").click();
  await expect(page.getByTestId("exam-msg")).toContainText("또 만나요");
  await expect(page.getByTestId("exam-msg")).not.toContainText("구독");

  // 챗봇
  await page.getByTestId("tab-chat").click();
  await page.getByTestId("chat-input").fill("學이 뭐예요?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("msg-bot").first()).toContainText("學");

  // 부모: 리포트 (기본은 무료, 상세는 구독 전용)
  await page.getByRole("button", { name: "나가기" }).click();
  await page.getByTestId("tab-report").click();
  await expect(page.getByTestId("basic-report")).toContainText("학습 시간");
  await page.getByTestId("detail-report").click();
  await expect(page.getByTestId("detail-msg")).toContainText("구독 전용");
});

test("준급: learning works, mock exam start is blocked as 형식 준비 중", async ({ page }) => {
  await signup(page, "jun@example.com");
  await page.getByTestId("nick").fill("준이");
  await page.getByTestId("pin").fill("1234");
  await page.getByTestId("body").selectOption("daegeom");
  await page.getByTestId("grade").selectOption("준5");
  await expect(page.getByTestId("grade-note")).toBeVisible();
  await page.getByTestId("add-child").click();
  await page.getByTestId("enter-준이").click();
  await page.getByTestId("child-pin").fill("1234");
  await page.getByTestId("child-unlock").click();
  await expect(page.getByTestId("today-card").first()).toBeVisible();
  await page.getByTestId("tab-exam").click();
  await page.getByTestId("exam-start").click();
  await expect(page.getByTestId("exam-msg")).toContainText("형식 준비 중");
});
