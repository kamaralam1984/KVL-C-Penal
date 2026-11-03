import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Dev-only seeded accounts (backend/scripts/dev_seed.py writes .dev-credentials, which is git-ignored). */
export function devCreds() {
  const txt = readFileSync(path.resolve(__dirname, "../../backend/.dev-credentials"), "utf8");
  const password = /password:\s*(\S+)/.exec(txt)![1];
  const emails = Object.fromEntries([...txt.matchAll(/^(\w+)\s+(\S+@\S+)$/gm)].map((m) => [m[1], m[2]]));
  return { password, emails } as { password: string; emails: Record<string, string> };
}

export async function login(page: Page, email: string, password: string, opts: { expectSuccess?: boolean } = { expectSuccess: true }) {
  await page.goto("/login");
  await page.waitForLoadState("load");
  await page.getByLabel("Email").first().fill(email);
  await page.getByLabel(/^Password/).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  if (opts.expectSuccess) await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 45_000 });
}

/** Open the account menu and sign out. Retries because the first click can land before hydration finishes. */
export async function signOut(page: Page) {
  await expect(async () => {
    await page.getByRole("button", { name: "Account menu" }).click();
    await expect(page.getByRole("menuitem", { name: "Sign out" })).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 30_000 });
  await page.getByRole("menuitem", { name: "Sign out" }).click();
}

export async function latestCode(request: APIRequestContext, email: string) {
  const r = await request.get(`/api/v1/dev/outbox?email=${encodeURIComponent(email)}`);
  expect(r.ok()).toBeTruthy();
  const rows = (await r.json()) as { body: string }[];
  return /(\d{6})/.exec(rows[0].body)![1];
}
