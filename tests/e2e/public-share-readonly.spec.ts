/**
 * E2E — Public share view is read-only.
 *
 * Group 9 from the plan.  Verifies that `/r/[shareId]` does not render any
 * manage UI (Exclude / Promote icons, hidden banner, Manage popover).
 *
 * Required environment:
 *   E2E_SHARE_ID — a public share id with comparable listings.
 */

import { expect, test } from "@playwright/test";

const SHARE_ID = process.env.E2E_SHARE_ID ?? "";

test.describe("Public share view — completely free of manage UI", () => {
  test.skip(!SHARE_ID, "set E2E_SHARE_ID to enable share-view test");

  test.beforeEach(async ({ page }) => {
    // Navigate as anonymous (no auth state).
    await page.goto(`/r/${SHARE_ID}`);
    await page
      .locator('[data-testid="comparable-card"]')
      .first()
      .waitFor({ state: "visible", timeout: 20_000 });
  });

  test("no Exclude / Promote / overflow / banner / Manage on share view", async ({
    page,
  }) => {
    // None of these should be in the DOM (not just hidden).
    await expect(page.locator('[data-testid="comp-action-exclude"]')).toHaveCount(
      0
    );
    await expect(page.locator('[data-testid="comp-action-promote"]')).toHaveCount(
      0
    );
    await expect(page.locator('[data-testid="comp-action-overflow"]')).toHaveCount(
      0
    );
    await expect(page.locator('[data-testid="hidden-banner"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="banner-manage"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="rerun-report-button"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="manage-panel"]')).toHaveCount(0);
  });

  test("hovering a card does not reveal action icons", async ({ page }) => {
    const card = page.locator('[data-testid="comparable-card"]').first();
    await card.hover();
    // Even after hover, action icons should not exist for share viewers.
    await expect(card.locator('[data-testid="comp-action-exclude"]')).toHaveCount(
      0
    );
    await expect(card.locator('[data-testid="comp-action-promote"]')).toHaveCount(
      0
    );
  });

  test("comp cards still render report data normally", async ({ page }) => {
    const cards = page.locator('[data-testid="comparable-card"]');
    expect(await cards.count()).toBeGreaterThan(0);
    // First card should have a View link and price text.
    const first = cards.first();
    await expect(first.locator('a:has-text("View")')).toBeVisible();
  });
});
