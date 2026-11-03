import { expect, test } from "@playwright/test";
import { devCreds, login } from "./helpers";

test("phone layout behaves like an app: bottom tabs, no horizontal scroll, theme switch", async ({ page }) => {
  const { password, emails } = devCreds();
  await login(page, emails.customer, password);
  await expect(page).toHaveURL(/customer\/dashboard/);
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Sidebar" })).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await nav.getByRole("link", { name: "Billing" }).click();
  await expect(page).toHaveURL(/customer\/billing/);
  await nav.getByRole("button", { name: "More navigation" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Change theme" }).click();
  await page.getByRole("menuitem", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("touch targets in the tab bar are at least 44px", async ({ page }) => {
  const { password, emails } = devCreds();
  await login(page, emails.customer, password);
  await page.waitForURL(/customer\/dashboard/);
  const boxes = await page.getByRole("navigation", { name: "Primary" }).locator("a,button").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height));
  for (const h of boxes) expect(h).toBeGreaterThanOrEqual(44);
});
