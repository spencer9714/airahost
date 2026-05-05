/**
 * Playwright auth setup — logs in once and persists storageState.
 *
 * The chromium-authenticated project depends on this and reuses the saved
 * cookies/local-storage so individual specs don't need to log in.
 *
 * Required env:
 *   E2E_USER_EMAIL
 *   E2E_USER_PASSWORD
 *
 * Storage path: tests/e2e/.auth/user.json (gitignored).
 */

import { expect, test as setup } from "@playwright/test";
import path from "node:path";
import fs from "node:fs";

const authDir = path.join(__dirname, ".auth");
const authFile = path.join(authDir, "user.json");

setup("authenticate via /login", async ({ page }) => {
  const email = process.env.E2E_USER_EMAIL;
  const password = process.env.E2E_USER_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "Missing E2E_USER_EMAIL / E2E_USER_PASSWORD.  Set them in your shell " +
        "or a .env file the runner can load before running `npm run test:e2e`."
    );
  }

  // Ensure the .auth directory exists before storageState() tries to write.
  if (!fs.existsSync(authDir)) {
    fs.mkdirSync(authDir, { recursive: true });
  }

  await page.goto("/login");

  // Labels are associated via htmlFor; getByLabel walks the accessibility tree.
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();

  // Success signal — Supabase auth round-trip → router.push("/dashboard").
  await page.waitForURL(/\/dashboard/, { timeout: 30_000 });

  // Sanity: dashboard page chrome rendered (not stuck on a redirect or error).
  await expect(page.locator("body")).not.toContainText(/Application error/i);

  await page.context().storageState({ path: authFile });
});
