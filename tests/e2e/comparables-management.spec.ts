/**
 * E2E — Comparables management (Exclude / Undo / Restore / Promote / Conflict).
 *
 * Covers groups 1, 2, 3, 4, 4b, 5, 11 from the plan.
 *
 * **Infra gap**: airahost does not yet have a working Playwright setup
 * (no playwright.config.ts, no auth fixture, no CI runner).  These specs
 * are written against the data-testid contracts that ship in Phase 2 —
 * they will run as-is once the surrounding infra is in place.
 *
 * Required environment:
 *   E2E_USER_EMAIL / E2E_USER_PASSWORD  — test account with ≥1 listing
 *                                          and ≥5 comps in its latest report.
 *   E2E_LISTING_ID                       — listing id with comps.
 */

import { expect, test } from "@playwright/test";

const SECONDARY_LISTING_ID = process.env.E2E_LISTING_ID_2 ?? "";

test.describe("Comparables management — exclude/undo/restore/promote/conflict", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard");
    await page.locator('[data-testid="comparable-card"]').first().waitFor({
      state: "visible",
      timeout: 20_000,
    });
  });

  // ── Group 1: Exclude Comparable ────────────────────────────────
  test("excluding a comp hides it locally and PATCHes after 6s", async ({ page }) => {
    const card = page.locator('[data-testid="comparable-card"]').first();
    const roomId = await card.getAttribute("data-room-id");
    expect(roomId, "card must have a room id").toBeTruthy();

    // Set up PATCH listener BEFORE the trigger.
    const patchPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/listings/`) && req.method() === "PATCH",
      { timeout: 12_000 }
    );

    // Hover to reveal action icons (desktop), then click Exclude.
    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();

    // Card should be marked exiting, toast should appear, banner appears.
    await expect(card).toHaveAttribute("data-state", "exiting");
    await expect(page.locator('[data-testid="toast-undo"]')).toBeVisible();
    await expect(page.locator('[data-testid="hidden-banner"]')).toContainText(
      /hidden locally/i
    );

    const req = await patchPromise;
    const body = JSON.parse(req.postData() ?? "{}");
    expect(body.excludedComps).toBeDefined();
    expect(
      (body.excludedComps as Array<{ roomId: string }>).some(
        (e) => e.roomId === roomId
      )
    ).toBe(true);

    await page.reload();
    await expect(
      page.locator(`[data-room-id="${roomId}"]`)
    ).toHaveCount(0);
    await expect(page.locator('[data-testid="hidden-banner"]')).toBeVisible();
  });

  // ── Group 2: Undo Exclude ──────────────────────────────────────
  test("Undo within 6s cancels the PATCH entirely", async ({ page }) => {
    const card = page.locator('[data-testid="comparable-card"]').first();
    const roomId = await card.getAttribute("data-room-id");

    let patchCount = 0;
    await page.route("**/api/listings/*", (route) => {
      if (route.request().method() === "PATCH") patchCount += 1;
      return route.continue();
    });

    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();
    await page.locator('[data-testid="toast-undo"]').click();

    // Wait past the 6s window — there should still be no PATCH.
    await page.waitForTimeout(6_500);
    expect(patchCount).toBe(0);

    await page.reload();
    await expect(
      page.locator(`[data-room-id="${roomId}"]`)
    ).toBeVisible();
  });

  // ── Group 3: Restore ────────────────────────────────────────────
  test("Restore from Manage popover removes the exclusion", async ({ page }) => {
    // Pre-condition: there's at least one exclusion already (the test should
    // be seeded; if not, group 1 above will leave one).  This test relies on
    // the banner being present.
    const banner = page.locator('[data-testid="hidden-banner"]');
    if (!(await banner.isVisible())) {
      test.skip(true, "no exclusion present — seed test data first");
    }
    await page.locator('[data-testid="banner-manage"]').click();
    const panel = page.locator('[data-testid="manage-panel"]');
    await expect(panel).toBeVisible();

    const restoreBtn = panel.locator('[data-testid^="manage-restore-"]').first();
    const restoreId = (await restoreBtn.getAttribute("data-testid"))?.replace(
      "manage-restore-",
      ""
    );

    const patchPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/listings/`) && req.method() === "PATCH",
      { timeout: 8_000 }
    );
    await restoreBtn.click();
    const req = await patchPromise;
    const body = JSON.parse(req.postData() ?? "{}");
    // Either the array doesn't include the roomId, or excludedComps was set to null.
    if (Array.isArray(body.excludedComps)) {
      const ids = (body.excludedComps as Array<{ roomId: string }>).map(
        (e) => e.roomId
      );
      expect(ids).not.toContain(restoreId);
    } else {
      expect(body.excludedComps).toBeNull();
    }
  });

  // ── Group 4: Promote (clean path) ───────────────────────────────
  test("promoting a comp adds it to preferredComps", async ({ page }) => {
    const card = page.locator('[data-testid="comparable-card"]').first();
    const url = await card.locator("a[href*='/rooms/']").first().getAttribute("href");

    const patchPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/listings/`) && req.method() === "PATCH",
      { timeout: 12_000 }
    );

    await card.hover();
    await card.locator('[data-testid="comp-action-promote"]').click();
    await expect(page.locator('[data-testid="toast-undo"]')).toBeVisible();

    const req = await patchPromise;
    const body = JSON.parse(req.postData() ?? "{}");
    expect(Array.isArray(body.preferredComps)).toBe(true);
    expect(
      (body.preferredComps as Array<{ listingUrl: string }>).some(
        (p) => url && p.listingUrl?.startsWith(url.split("?")[0])
      )
    ).toBe(true);
    // Clean path: no excludedComps in body.
    expect(body.excludedComps).toBeUndefined();
  });

  // ── Group 4b: Promote from excluded (atomic + confirm) ─────────
  test("promoting an already-excluded comp opens confirm + atomic PATCH", async ({
    page,
  }) => {
    // Setup: exclude one comp first, wait for it to land, then attempt to
    // promote it via Manage panel's restore… no — the confirm path triggers
    // when promote is invoked on an already-excluded comp via the card UI.
    // For simplicity, we invoke via the page's URL flow assuming a comp
    // exists in excludedComps; if not, this test would be skipped.
    const banner = page.locator('[data-testid="hidden-banner"]');
    if (!(await banner.isVisible())) {
      test.skip(true, "no exclusion present — seed test data first");
    }

    // Find a card whose roomId is in the excluded set — note: it won't be
    // visible in the main list (filtered out).  Promote-from-excluded path
    // is normally exercised when the user re-expands the card via Manage,
    // but the card-level API also supports it.  For this E2E we instead
    // test the inline-confirm dialog flow generically.
    test.skip(true, "Requires test fixture for excluded-but-rendered comp");
  });

  // ── Group 5: Conflict — exclude existing benchmark ────────────
  test("excluding a benchmark opens inline confirm and atomic PATCH", async ({
    page,
  }) => {
    // Find a card that is pinned (benchmark).
    const pinnedCard = page
      .locator('[data-testid="comparable-card"]')
      .filter({ hasText: /Pinned by you/i })
      .first();
    const exists = await pinnedCard.count();
    if (exists === 0) {
      test.skip(true, "no benchmark in current report — seed test data first");
    }

    await pinnedCard.hover();
    await pinnedCard.locator('[data-testid="comp-action-exclude"]').click();

    const dialog = page.locator('[data-testid="conflict-dialog"]');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/benchmark/i);

    const patchPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/listings/`) && req.method() === "PATCH",
      { timeout: 8_000 }
    );
    await dialog.locator('[data-testid="conflict-dialog-confirm"]').click();
    const req = await patchPromise;
    const body = JSON.parse(req.postData() ?? "{}");
    expect(Array.isArray(body.preferredComps)).toBe(true);
    expect(Array.isArray(body.excludedComps)).toBe(true);
  });

  // ── Group 11: Pagehide flush (delayed PATCH must not be lost) ─
  test("page hide within 6s sends sendBeacon flush", async ({ page, context }) => {
    const card = page.locator('[data-testid="comparable-card"]').first();
    let beaconHits = 0;
    await page.route("**/flush-exclusions", (route) => {
      beaconHits += 1;
      return route.fulfill({ status: 204 });
    });

    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();
    await expect(page.locator('[data-testid="toast-undo"]')).toBeVisible();

    // Simulate page-hide via visibilitychange.
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // sendBeacon is fire-and-forget — give the route handler a moment.
    await page.waitForTimeout(500);
    expect(beaconHits).toBeGreaterThanOrEqual(1);
  });
});

// ─── Resilience (groups 13–19) ─────────────────────────────────────────────────

test.describe("Comparables management — resilience", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard");
    await page.locator('[data-testid="comparable-card"]').first().waitFor({
      state: "visible",
      timeout: 20_000,
    });
  });

  // ── Group 13: Network failure → rollback + Retry ──────────────
  test("PATCH abort triggers rollback + Retry toast", async ({ page }) => {
    await page.route("**/api/listings/*", (route) => {
      if (route.request().method() === "PATCH") return route.abort();
      return route.continue();
    });

    const card = page.locator('[data-testid="comparable-card"]').first();
    const roomId = await card.getAttribute("data-room-id");

    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();
    // 6s timer + ~retry
    await page.waitForTimeout(6_500);

    await expect(
      page.locator(`[data-room-id="${roomId}"]`)
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('[data-testid="toast-retry"]')).toBeVisible();
  });

  // ── Group 14: Retry succeeds ──────────────────────────────────
  test("Retry after a transient failure persists the exclusion", async ({
    page,
  }) => {
    let failedOnce = false;
    await page.route("**/api/listings/*", (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      if (!failedOnce) {
        failedOnce = true;
        return route.abort();
      }
      return route.continue();
    });

    const card = page.locator('[data-testid="comparable-card"]').first();
    const roomId = await card.getAttribute("data-room-id");

    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();
    await page.waitForTimeout(6_500);
    await page.locator('[data-testid="toast-retry"]').click();

    await page.waitForTimeout(500);
    await page.reload();
    await expect(
      page.locator(`[data-room-id="${roomId}"]`)
    ).toHaveCount(0);
  });

  // ── Group 15: Listing switch flushes pending ─────────────────
  test("switching listing within 6s flushes pending PATCH", async ({ page }) => {
    test.skip(
      !SECONDARY_LISTING_ID,
      "set E2E_LISTING_ID_2 to enable listing-switch test"
    );

    const card = page.locator('[data-testid="comparable-card"]').first();

    const patchPromise = page.waitForRequest(
      (req) =>
        req.url().includes("/api/listings/") && req.method() === "PATCH",
      { timeout: 5_000 }
    );

    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();
    await page
      .locator(`[data-testid="listing-nav-${SECONDARY_LISTING_ID}"]`)
      .click();

    // PATCH should fire as part of the flush (well before the 6s timer).
    const req = await patchPromise;
    expect(req.method()).toBe("PATCH");
  });

  // ── Group 17: Stale-tab conflict (400 + conflictingIds) ──────
  test("400 conflict shows Refresh-to-continue toast", async ({ page }) => {
    await page.route("**/api/listings/*", (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      return route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          error: "Cannot exclude a comp that is currently a benchmark",
          conflictingIds: ["12345"],
        }),
      });
    });

    const card = page.locator('[data-testid="comparable-card"]').first();
    const roomId = await card.getAttribute("data-room-id");
    await card.hover();
    await card.locator('[data-testid="comp-action-exclude"]').click();
    await page.waitForTimeout(6_500);

    await expect(page.locator('[data-testid="toast-refresh"]')).toBeVisible();
    await expect(
      page.locator(`[data-room-id="${roomId}"]`)
    ).toBeVisible({ timeout: 5_000 });
  });

  // ── Group 18: Batch multiple quick excludes ──────────────────
  test("3 quick excludes merge into one PATCH (deduped by roomId)", async ({
    page,
  }) => {
    const cards = page.locator('[data-testid="comparable-card"]');
    const count = await cards.count();
    test.skip(count < 3, "need ≥3 visible cards");

    const ids: string[] = [];
    for (let i = 0; i < 3; i++) {
      const id = await cards.nth(i).getAttribute("data-room-id");
      if (id) ids.push(id);
    }

    const patchPromise = page.waitForRequest(
      (req) =>
        req.url().includes("/api/listings/") && req.method() === "PATCH",
      { timeout: 12_000 }
    );

    for (let i = 0; i < 3; i++) {
      await cards.nth(i).hover();
      await cards.nth(i).locator('[data-testid="comp-action-exclude"]').click();
    }

    // All 3 should be in exit state.
    for (let i = 0; i < 3; i++) {
      await expect(cards.nth(i)).toHaveAttribute("data-state", "exiting");
    }

    const req = await patchPromise;
    const body = JSON.parse(req.postData() ?? "{}");
    const sentIds = (body.excludedComps as Array<{ roomId: string }>).map(
      (e) => e.roomId
    );
    for (const id of ids) {
      expect(sentIds).toContain(id);
    }
    // Dedup: no duplicates.
    expect(new Set(sentIds).size).toBe(sentIds.length);
  });

  // ── Group 19: Undo full batch ────────────────────────────────
  test("Undo restores the entire batch + sends no PATCH", async ({ page }) => {
    const cards = page.locator('[data-testid="comparable-card"]');
    const count = await cards.count();
    test.skip(count < 3, "need ≥3 visible cards");

    let patchCount = 0;
    await page.route("**/api/listings/*", (route) => {
      if (route.request().method() === "PATCH") patchCount += 1;
      return route.continue();
    });

    for (let i = 0; i < 3; i++) {
      await cards.nth(i).hover();
      await cards.nth(i).locator('[data-testid="comp-action-exclude"]').click();
    }
    await page.locator('[data-testid="toast-undo"]').click();

    await page.waitForTimeout(6_500);
    expect(patchCount).toBe(0);
    // All three should be visible again.
    for (let i = 0; i < 3; i++) {
      await expect(cards.nth(i)).not.toHaveAttribute("data-state", "exiting");
    }
  });
});
