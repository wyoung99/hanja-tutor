import { expect, Page } from "@playwright/test";

// Accounts: parent signs up on /signup (id + password, no phone verification), creates the child account
// (id + password + nickname); everybody logs in on the landing page and the server decides the role.

export const PARENT_PW = "parent-pw-1";
export const CHILD_PW = "kid1234";
let seq = 0;
/** Unique login id (4-20 chars) -- the E2E database lives for the whole run. */
export const uid = (p: string) => `${p}${(Date.now() % 1e7).toString(36)}${(seq++).toString(36)}`;

export type Pt = [number, number];

export async function draw(page: Page, pts: Pt[]) {
  const box = (await page.getByTestId("stroke-pad").first().boundingBox())!;
  const at = ([x, y]: Pt) => [box.x + (x / 1000) * box.width, box.y + (y / 1000) * box.height] as const;
  await page.mouse.move(...at(pts[0]));
  await page.mouse.down();
  for (const p of pts.slice(1)) await page.mouse.move(...at(p), { steps: 6 });
  await page.mouse.up();
}

/** Parent sign-up on the separate /signup page; it logs in and lands on the parent screen. */
export async function signup(page: Page, loginId: string, email = `${loginId}@example.com`) {
  await page.goto("/signup");
  await page.getByTestId("signup-id").fill(loginId);
  await page.getByTestId("signup-password").fill(PARENT_PW);
  await page.getByTestId("signup-confirm").fill(PARENT_PW);
  await page.getByTestId("email").fill(email);
  for (const t of ["agree-terms", "agree-privacy", "agree-guardian"]) await page.getByTestId(t).check();
  await page.getByTestId("signup").click();
  await expect(page.getByTestId("parent-menu")).toBeVisible();
}

/** The one login form on the landing page (parents and children). */
export async function login(page: Page, loginId: string, password: string) {
  await page.goto("/");
  await page.getByTestId("login-id").fill(loginId);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login").click();
}

export async function parentLogin(page: Page, loginId: string) {
  await login(page, loginId, PARENT_PW);
  await expect(page.getByTestId("parent-menu")).toBeVisible();
}

/** Child login; resolves to the child's account id (from the first heartbeat). */
export async function childLogin(page: Page, loginId: string, password = CHILD_PW): Promise<string> {
  const hb = page.waitForResponse((r) => r.url().includes("/heartbeat"));
  await login(page, loginId, password);
  await expect(page.getByTestId("child-menu")).toBeVisible();
  const url = (await hb).url();
  return url.split("/children/")[1].split("/")[0];
}

export async function logout(page: Page) {
  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login-form")).toBeVisible();
}

export async function addChild(page: Page, nick: string, loginId: string,
  opts: { consent?: boolean; body?: string; grade?: string; password?: string } = {}) {
  await page.getByTestId("tab-kids").click();
  await page.getByTestId("nick").fill(nick);
  await page.getByTestId("child-login-id").fill(loginId);
  await page.getByTestId("child-password").fill(opts.password ?? CHILD_PW);
  await page.getByTestId("body").selectOption(opts.body ?? "korea_eomunhoe");
  await page.getByTestId("grade").selectOption(opts.grade ?? "8");
  if (opts.consent ?? true) await page.getByTestId("ai-consent").check();
  await page.getByTestId("add-child").click();
  await expect(page.getByTestId(`login-id-${nick}`)).toContainText(loginId);
}

/** Authorization header of whoever is logged in on `page` (for direct API checks). */
export async function bearer(page: Page): Promise<Record<string, string>> {
  const token = await page.evaluate(() => JSON.parse(localStorage.getItem("hanja.auth") ?? "{}").token as string);
  return { Authorization: `Bearer ${token}` };
}

export async function shiftClock(page: Page, minutes: number) {
  const r = await page.request.post("/api/dev/clock", { data: { advance_minutes: minutes } });
  expect(r.ok()).toBeTruthy();
}
export const resetClock = (page: Page) => page.request.post("/api/dev/clock", { data: { reset: true } });
