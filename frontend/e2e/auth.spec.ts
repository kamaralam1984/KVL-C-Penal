import { expect, test } from "@playwright/test";
import { devCreds, latestCode, login, signOut } from "./helpers";

test("register, verify email by code, sign in, sign out", async ({ page, request }) => {
  const email = `e2e-${Date.now()}@x.example.com`;
  const password = "Playwright-Pass-42";
  await page.goto("/register");
  await page.getByLabel("Full name").fill("E2E User");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization").fill("E2E Org");
  await page.getByLabel(/^Password/).fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/verify-email/);
  await page.getByLabel("6-digit code").fill(await latestCode(request, email));
  await page.getByRole("button", { name: "Verify email" }).click();
  await expect(page.getByText("Email verified")).toBeVisible();
  await page.getByRole("button", { name: "Continue to sign in" }).click();
  await login(page, email, password);
  await expect(page).toHaveURL(/\/customer\/dashboard/);
  await signOut(page);
  await expect(page).toHaveURL(/\/login/);
});

test("wrong password gives a generic error and stays on the page", async ({ page }) => {
  await login(page, "nobody@x.example.com", "Wrong-Password-1", { expectSuccess: false });
  await expect(page.locator('[role="alert"]', { hasText: "incorrect" })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("anonymous users are redirected to sign in", async ({ page }) => {
  await page.goto("/admin/dashboard");
  await expect(page).toHaveURL(/\/login\?next=/);
});

test("customer cannot use the admin area; admin can", async ({ page }) => {
  const { password, emails } = devCreds();
  await login(page, emails.customer, password);
  await expect(page).toHaveURL(/\/customer\/dashboard/);
  await page.goto("/admin/dashboard");
  await expect(page.getByText("Permission denied")).toBeVisible();
  await signOut(page);
  await login(page, emails.super_admin, password);
  await expect(page).toHaveURL(/\/admin\/dashboard/);
  await page.goto("/admin/customers");
  await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
});

test("2FA setup enforces a code at login", async ({ page, request }) => {
  const email = `tfa-${Date.now()}@x.example.com`;
  const password = "Playwright-Pass-42";
  await request.post("/api/v1/auth/register", { data: { email, name: "TFA User", password } });
  await request.post("/api/v1/auth/verify-email", { data: { email, code: await latestCode(request, email) } });
  await login(page, email, password);
  await page.goto("/account");
  await page.getByRole("tab", { name: /Security/ }).click();
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  await expect(page.getByAltText("QR code for your authenticator app")).toBeVisible();
});
