import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { devCreds, login } from "./helpers";

const serious = (v: { impact?: string | null }[]) => v.filter((x) => x.impact === "serious" || x.impact === "critical");

for (const path of ["/login", "/register", "/"]) {
  test(`no serious a11y violations on ${path}`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("load");
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(serious(r.violations), JSON.stringify(r.violations.map((v) => v.id))).toEqual([]);
  });
}

test("no serious a11y violations on the admin dashboard (light and dark)", async ({ page }) => {
  const { password, emails } = devCreds();
  await login(page, emails.super_admin, password);
  await page.waitForURL(/admin\/dashboard/);
  await page.waitForLoadState("load");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((t) => localStorage.setItem("theme", t), theme);
    await page.reload();
    await page.waitForLoadState("load");
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(serious(r.violations), `${theme}: ${JSON.stringify(r.violations.map((v) => v.id))}`).toEqual([]);
  }
});
