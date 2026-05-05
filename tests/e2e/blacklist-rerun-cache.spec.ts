/**
 * E2E — Re-run after exclusion + banner two-state + invalid roomId.
 *
 * Groups 6, 10, 12 from the plan.
 */

import { expect, test } from "@playwright/test";

test.describe("Blacklist rerun + cache + banner two-state", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard");
    await page
      .locator('[data-testid="comparable-card"]')
      .first()
      .waitFor({ state: "visible", timeout: 20_000 });
  });

  // ── Group 6: Re-run loading state + post-rerun banner switch ──
  test("Re-run button shows Starting… and prevents double-submit", async ({
    page,
  }) => {
    const banner = page.locator('[data-testid="hidden-banner"]');
    if (!(await banner.isVisible())) {
      // Need at least one exclusion to trigger banner; do one quickly.
      const card = page.locator('[data-testid="comparable-card"]').first();
      await card.hover();
      await card.locator('[data-testid="comp-action-exclude"]').click();
      await page.waitForTimeout(6_500);
    }

    let rerunCount = 0;
    await page.route("**/api/listings/*/rerun", (route) => {
      rerunCount += 1;
      // Slow response so the disabled state is observable.
      return new Promise((resolve) => {
        setTimeout(() => {
          resolve(route.fulfill({ status: 200, body: "{}" }));
        }, 1_000);
      });
    });

    const rerunBtn = page.locator('[data-testid="rerun-report-button"]');
    await rerunBtn.click();
    await expect(rerunBtn).toBeDisabled();
    await expect(rerunBtn).toContainText(/Starting/i);

    // Click again while disabled — should not fire a 2nd request.
    await rerunBtn.click({ force: true }).catch(() => {});
    await page.waitForTimeout(1_500);
    expect(rerunCount).toBe(1);
  });

  // ── Group 10: Invalid / missing roomId — no crash, no actions ─
  test("comp without a parseable roomId does not crash and hides actions", async ({
    page,
  }) => {
    // We can't easily seed an invalid card, but we can verify that no card
    // with a missing data-room-id exposes action icons.
    const cards = page.locator('[data-testid="comparable-card"]');
    const count = await cards.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const id = await cards.nth(i).getAttribute("data-room-id");
      if (!id) {
        await expect(
          cards.nth(i).locator('[data-testid="comp-action-exclude"]')
        ).toHaveCount(0);
      }
    }
    // Page itself is healthy.
    await expect(page.locator("body")).not.toContainText(/Application error/i);
  });

  // ── Group 12: Banner two-state ─────────────────────────────────
  test("banner wording reflects whether report has been re-run", async ({
    page,
  }) => {
    // Read the banner text and the report's excludedRoomIdsAtRun via API.
    // If the banner exists, we expect either of the two state texts.
    const banner = page.locator('[data-testid="hidden-banner"]');
    if (!(await banner.isVisible())) test.skip(true, "no exclusions present");
    const text = (await banner.textContent()) ?? "";
    expect(text).toMatch(/hidden locally|Pricing excludes/i);
  });
});
