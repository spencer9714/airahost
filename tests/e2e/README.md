# Playwright E2E (airahost)

End-to-end tests for the comp-blacklist + benchmark-editor surfaces shipped
in Phases 1–3.  Specs live alongside production code; the runner is
configured at the repo root in `playwright.config.ts`.

---

## Quick start

```bash
# 1. Install Playwright + browser binary (first time only).
npm install
npm run test:e2e:install

# 2. Set credentials for the test account.  (Bash / WSL / Git Bash.)
export E2E_USER_EMAIL="qa@example.com"
export E2E_USER_PASSWORD="…"

# 3. Run.  Auto-starts `next dev` on 127.0.0.1:3000 if no E2E_BASE_URL.
npm run test:e2e
```

PowerShell / cmd users:

```powershell
$env:E2E_USER_EMAIL = "qa@example.com"
$env:E2E_USER_PASSWORD = "…"
npm run test:e2e
```

## All scripts

| Script                    | What it does                                                   |
|---------------------------|----------------------------------------------------------------|
| `npm run test:e2e:install`| `playwright install chromium` — fetches the browser binary.    |
| `npm run test:e2e`        | Runs all projects: `setup` → `chromium-authenticated` → `chromium-public`. |
| `npm run test:e2e:ui`     | Opens Playwright's interactive UI mode (run + debug specs visually). |
| `npm run test:e2e:headed` | Runs headed (browser visible).  Useful for debugging timing.   |
| `npm run test:e2e:public` | Runs only the `chromium-public` project (anonymous share view).|

## Required environment

| Variable                    | Required by                          | Notes                                                 |
|-----------------------------|--------------------------------------|-------------------------------------------------------|
| `E2E_USER_EMAIL`            | `auth.setup.ts`                      | Test account credentials.                             |
| `E2E_USER_PASSWORD`         | `auth.setup.ts`                      |                                                       |
| `E2E_BASE_URL`              | optional                             | Skip auto-start.  Default `http://127.0.0.1:3000`.    |
| `E2E_LISTING_ID`            | optional                             | Listing whose latest report has comps + benchmarks.   |
| `E2E_SECONDARY_LISTING_ID`  | optional (group 15: listing-switch)  | A second listing for the switch-flush spec.           |
| `E2E_SHARE_ID`              | optional (`public-share-readonly`)   | Public `shareId` for an existing pricing report.      |

If the optional env vars are unset, the relevant tests `skip()` themselves
with a clear reason rather than failing.

## Required seed data

Most specs assume the test account already has data.  Either run a few
manual reports first, or build a small seed script.

| Spec                              | Seed requirement                                                        |
|-----------------------------------|-------------------------------------------------------------------------|
| All authenticated specs           | Logged-in account with at least one listing.                            |
| `comparables-management.spec.ts`  | Dashboard shows ≥1 ready report with ≥5 comps.                          |
| `benchmark-editor.spec.ts` BE-1–6 | At least one listing with `preferredComps` populated.                   |
| `benchmark-editor.spec.ts` BE-7   | Edit panel can add a row + reorder (no extra seed).                     |
| `benchmark-editor.spec.ts` BE-8   | At least one listing with `excludedComps` populated.                    |
| `benchmark-editor.spec.ts` BE-9   | Listing with **exactly 10** `preferredComps` to trigger the cap path.   |
| `public-share-readonly.spec.ts`   | Valid `E2E_SHARE_ID`.                                                   |
| `blacklist-rerun-cache.spec.ts` 12| Listings whose `report.excludedRoomIdsAtRun` covers / doesn't cover     |
|                                   | current `excludedComps` — for the two-state banner test.                |

## Project layout

```
tests/e2e/
├── auth.setup.ts                    # Logs in once, persists storageState
├── .auth/user.json                  # Generated; gitignored
├── comparables-management.spec.ts   # Phase 2: exclude / promote / resilience
├── benchmark-editor.spec.ts         # Phase 3: editor + replace sheet
├── public-share-readonly.spec.ts    # Anonymous share view
├── blacklist-rerun-cache.spec.ts    # Re-run + banner two-state + invalid id
└── README.md                        # This file
```

`playwright.config.ts` defines three projects:

| Project                  | Runs                                  | storageState                  |
|--------------------------|---------------------------------------|-------------------------------|
| `setup`                  | `auth.setup.ts` only                  | (creates the file)            |
| `chromium-authenticated` | All `*.spec.ts` except share-readonly | `tests/e2e/.auth/user.json`   |
| `chromium-public`        | `public-share-readonly.spec.ts` only  | none (anonymous)              |

## CI

The runner respects standard `CI=true`:

- `forbidOnly: true` — `.only` is a hard failure
- `retries: 2`
- `workers: 1`
- HTML + GitHub reporters

Any CI step needs to:
1. Set `E2E_USER_EMAIL` / `E2E_USER_PASSWORD` from secrets
2. `npm run test:e2e:install`
3. `npm run test:e2e`

## Spec coverage

| Group | File                              | Status |
|-------|-----------------------------------|--------|
| 1     | `comparables-management.spec.ts` | ✓ — Exclude → 6s PATCH                                       |
| 2     | `comparables-management.spec.ts` | ✓ — Undo cancels PATCH entirely                              |
| 3     | `comparables-management.spec.ts` | ✓ — Restore via Manage popover                               |
| 4     | `comparables-management.spec.ts` | ✓ — Promote (clean path)                                     |
| 4b    | `comparables-management.spec.ts` | ⚠ — needs fixture for excluded-but-rendered comp             |
| 5     | `comparables-management.spec.ts` | ✓ — Conflict (exclude benchmark)                             |
| 6     | `blacklist-rerun-cache.spec.ts`  | ✓ — Re-run loading state                                     |
| 9     | `public-share-readonly.spec.ts`  | ✓ — Share view has no manage UI                              |
| 10    | `blacklist-rerun-cache.spec.ts`  | ✓ — Missing roomId no-crash                                  |
| 11    | `comparables-management.spec.ts` | ✓ — Pagehide flush via sendBeacon                            |
| 12    | `blacklist-rerun-cache.spec.ts`  | ✓ — Banner two-state wording                                 |
| 13    | `comparables-management.spec.ts` | ✓ — Network failure → rollback + Retry                       |
| 14    | `comparables-management.spec.ts` | ✓ — Retry succeeds                                           |
| 15    | `comparables-management.spec.ts` | ✓ — Listing-switch flush (needs `E2E_SECONDARY_LISTING_ID`)  |
| 16    | (deferred)                       | needs route-navigation fixture                               |
| 17    | `comparables-management.spec.ts` | ✓ — Stale-tab conflict (400)                                 |
| 18    | `comparables-management.spec.ts` | ✓ — Batch 3 quick excludes                                   |
| 19    | `comparables-management.spec.ts` | ✓ — Batch undo                                               |
| BE-1  | `benchmark-editor.spec.ts`       | ✓ — URL blur auto-title + auto-collapse + no PATCH           |
| BE-2  | `benchmark-editor.spec.ts`       | ✓ — Staged cancel                                            |
| BE-3  | `benchmark-editor.spec.ts`       | ✓ — Toggle disabled persists `enabled: false`                |
| BE-4  | `benchmark-editor.spec.ts`       | ✓ — Reorder via ••• menu persists                            |
| BE-5  | `benchmark-editor.spec.ts`       | ✓ — Remove + Undo restores; no PATCH                         |
| BE-6  | `benchmark-editor.spec.ts`       | ✓ — Remove + Save → Undo no-op                               |
| BE-7  | `benchmark-editor.spec.ts`       | ✓ — Title fetch race vs reorder (resolves by draftId)        |
| BE-8  | `benchmark-editor.spec.ts`       | ✓ — Excluded panel: Restore is staged                        |
| BE-9  | `benchmark-editor.spec.ts`       | ✓ — Replace sheet at 10-cap; API failure → error toast       |

## Stable testid contract

Production code is responsible for keeping these stable.  If you rename a
testid, update the spec in the same PR.

**Comparable card row** (`ComparableListingsSection.tsx`)
- `comparable-card` — root.  Carries `data-room-id` + `data-state` (`idle`/`exiting`).
- `comp-action-exclude`, `comp-action-promote` — desktop hover-revealed action icons
- `comp-action-overflow` — mobile `•••` button

**Banner / Manage popover** (`ComparableListingsSection.tsx`)
- `hidden-banner` — root of the "X comparables hidden" strip
- `rerun-report-button` — Re-run button inside the banner
- `banner-manage` — Manage link
- `manage-panel` — popover root
- `manage-row-{roomId}` — one row per excluded comp
- `manage-restore-{roomId}` — Restore button per row

**Conflict dialog** (inline, in `ComparableListingsSection.tsx`)
- `conflict-dialog`
- `conflict-dialog-confirm`
- `conflict-dialog-cancel`

**Toaster** (`Toaster.tsx`)
- `toaster` — root container
- `toast-undo`, `toast-retry`, `toast-refresh` — action-button testids
  passed via `toast({ action: { testId: ... } })`

**Replace sheet** (`ReplaceBenchmarkSheet.tsx`)
- `replace-benchmark-sheet` — dialog root
- `replace-benchmark-row-{idx}` — one row per existing benchmark
- `replace-benchmark-cancel`

**Benchmark editor row** (`ListingCard.tsx`)
- `benchmark-row` + `data-row-idx` — row container
- `benchmark-enabled-toggle` + `data-row-idx` — switch
- `benchmark-row-menu` + `data-row-idx` — `•••` button
- `benchmark-url-input` + `data-row-idx` — URL field
- `benchmark-move-up-{idx}`, `benchmark-move-down-{idx}`, `benchmark-remove-{idx}` — menu items

**Excluded comps accordion** (`ListingCard.tsx`)
- `excluded-comps-panel` — `<details>` root
- `excluded-comps-summary` — clickable header
- `excluded-row` + `data-room-id` — one per entry
- `excluded-restore-button` + `data-room-id` — Restore button per entry

**Listing nav** (`ListingCard.tsx`)
- `listing-nav-{listingId}` — outer selectable body of each card

## Troubleshooting

**`Error: missing E2E_USER_EMAIL`**
Set the credentials in your shell, then re-run.  See "Quick start" above.

**`Timed out 30000ms waiting for navigation to /dashboard`**
The login form failed.  Run with `--headed` (`npm run test:e2e:headed`) to
see what happened.  Common causes: wrong password, Supabase rate limit,
the test account requires email confirmation.

**`No tests found` for benchmark-editor BE-9**
Your seeded listing has fewer than 10 `preferredComps`.  Either seed 10 or
accept the `test.skip()`.

**`The default browser is missing`**
Run `npm run test:e2e:install` first.

**Auth state stale after a password reset**
Delete `tests/e2e/.auth/user.json` and re-run.
