# Co-host Full Access Verification Design

## Goal

Add a real verification path for Airahost co-host setup so the dashboard can determine whether the Airbnb account:

- email: `ashway14721@gmail.com`
- Airbnb user id: `584480104`

has been added to a given listing as a co-host and whether that co-host has `Full access`.

This should replace the current stubbed verification flow used by `POST /api/listings/[id]/cohost-verify` and `src/lib/cohostVerification.ts`.

Important requirement:

- the co-host email and Airbnb user id must be passed as arguments to the verification logic
- they must not be hardcoded into the verifier
- the current Airahost account above is only the initial example/default account

## Current State

The project already has the right product scaffolding:

- `src/lib/cohostVerification.ts`
  - defines the verification contract but currently returns `verification_pending` with `method: "stub"`
- `src/app/api/listings/[id]/cohost-verify/route.ts`
  - records user confirmation and calls `startCohostVerification(...)`
- `supabase/migrations/020_cohost_verification_model.sql`
  - already provides a status model: `not_started`, `invite_opened`, `user_confirmed`, `verification_pending`, `verified`, `verification_failed`
- `worker/README.md`
  - shows the worker already connects to a locally logged-in Chrome session through CDP

That existing worker/browser pattern is the strongest fit for this feature because the Airbnb co-host page is behind host authentication and depends on a real logged-in Airbnb session.

## Verification Target

For a listing and Airbnb co-host user id, the canonical detail page is:

```text
https://www.airbnb.com/hosting/listings/editor/{listing_id}/details/co-hosts/{user_id}
```

Example:

```text
https://www.airbnb.com/hosting/listings/editor/1596737613274892756/details/co-hosts/290669168
```

The page exposes the fields we care about:

- `Permissions`
- `Payouts`
- `Primary Host`

The verification decision for Auto-Apply should be:

- `verified` when:
  - the page loads successfully for the target co-host user id
  - the `Permissions` value is `Full access`
- `verification_failed` otherwise

We should also capture the other two visible fields for audit/debugging:

- `Payouts`
- `Primary Host`

## Recommended Approach

Use an authenticated browser-session check backed by the local worker CDP browser.

Why this is the right choice:

- the page is private and requires an Airbnb host session
- the project already has a local authenticated browser pattern for Airbnb work
- this avoids pretending there is a stable public Airbnb API for co-host permissions
- it lets us verify the exact UI state the host sees for a specific co-host user id

## Proposed Design

### 1. Add a worker-side verifier

Introduce a worker utility that opens the co-host detail page and extracts the three field values.

Suggested Python shape:

```py
def verify_cohost_access(
    *,
    listing_id: str,
    cohost_email: str,
    cohost_user_id: str,
    cdp_url: str,
) -> dict:
    ...
```

Suggested return payload:

```json
{
  "ok": true,
  "listingId": "1596737613274892756",
  "cohostEmail": "ashway14721@gmail.com",
  "cohostUserId": "290669168",
  "pageUrl": "https://www.airbnb.com/hosting/listings/editor/1596737613274892756/details/co-hosts/290669168",
  "exists": true,
  "permissionsLabel": "Full access",
  "payoutsLabel": "Not set up",
  "primaryHostLabel": "No",
  "hasFullAccess": true,
  "verifiedAt": "2026-04-23T12:34:56.000Z",
  "method": "browser_session"
}
```

Failure example:

```json
{
  "ok": false,
  "cohostEmail": "ashway14721@gmail.com",
  "exists": false,
  "hasFullAccess": false,
  "errorCode": "cohost_not_found",
  "errorMessage": "The target Airbnb co-host was not found on the listing.",
  "method": "browser_session"
}
```

### 2. Replace the TypeScript stub with a real integration boundary

`src/lib/cohostVerification.ts` should stop self-returning a stubbed result and instead call a worker-backed verification path.

Recommended TypeScript contract:

```ts
export interface CohostVerificationDetails {
  pageUrl: string;
  airbnbListingId: string;
  targetEmail: string;
  targetUserId: string;
  permissionsLabel: string | null;
  payoutsLabel: string | null;
  primaryHostLabel: string | null;
  hasFullAccess: boolean;
}
```

`CohostVerificationResult` should be extended to include:

- `details?: CohostVerificationDetails | null`

This gives the dashboard and logs a structured record of what was actually observed.

### 3. Keep the existing API route

Keep `POST /api/listings/[id]/cohost-verify` as the app-facing entrypoint.

Its behavior should become:

1. Auth + ownership check
2. Read listing URL from `input_attributes`
3. Extract Airbnb listing id
4. Resolve the target co-host account arguments:
   - `cohostEmail`
   - `cohostUserId`
5. Call real co-host verification with those arguments
6. Persist:
   - `auto_apply_cohost_status`
   - `auto_apply_cohost_confirmed_at`
   - `auto_apply_cohost_verified_at`
   - `auto_apply_cohost_verification_error`
   - `auto_apply_cohost_verification_method = "browser_session"`
7. Return structured verification data

## Selector and Parsing Strategy

The verifier should treat the Airbnb co-host detail page as the source of truth.

Target page sections:

- `Permissions`
- `Payouts`
- `Primary Host`

Expected extraction result from the screenshot example:

- `Permissions` -> `Full access`
- `Payouts` -> `Not set up`
- `Primary Host` -> `No`

Recommended extraction strategy:

1. Navigate directly to:
   `https://www.airbnb.com/hosting/listings/editor/{listing_id}/details/co-hosts/{user_id}`
2. Wait for stable page content
3. Confirm we are not on:
   - login page
   - access denied page
   - generic co-host list page without the target detail view
4. Locate the visible headings:
   - `Permissions`
   - `Payouts`
   - `Primary Host`
5. Read the primary value shown under each heading
6. Normalize into booleans and stored labels

Normalization rule for permissions:

```text
hasFullAccess = normalize(Permissions) === "full access"
```

Normalization rule for the overall verification outcome:

- `verified` if `exists === true` and `hasFullAccess === true`
- `verification_failed` otherwise

## Failure Modes

The verifier should distinguish these cases instead of returning a generic failure:

- `listing_id_missing`
  - the saved listing does not contain a usable Airbnb listing URL/id
- `browser_auth_required`
  - the CDP browser is not logged into Airbnb hosting
- `cohost_not_found`
  - the target user id page is unavailable or redirects away because the co-host is not on the listing
- `permissions_not_full_access`
  - co-host exists, but `Permissions` is not `Full access`
- `page_parse_failed`
  - the page loaded but expected fields could not be extracted reliably
- `navigation_failed`
  - browser navigation timed out or page load failed

These should map to `auto_apply_cohost_verification_error` as human-readable text.

## Configuration

Do not hardcode the account identity inside page logic.

Recommended env vars:

- `NEXT_PUBLIC_AIRAHOST_COHOST_EMAIL=ashway14721@gmail.com`
  - already useful for invite/setup UX
- `AIRAHOST_COHOST_TARGET_USER_ID=584480104`
  - backend/worker-only default verification target

Recommended runtime contract:

- `cohostEmail` and `cohostUserId` are explicit function arguments at each verification boundary
- env vars may provide defaults for the current Airahost account
- callers should be able to override those defaults so the same verifier works for other co-host accounts later

Optional future env:

- `AIRAHOST_COHOST_TARGET_NAME`
  - useful only for debugging or UI copy

## Data Model Changes

Cache decision for this design:

- Primary cache: Supabase
- Best structure: dedicated `listing_cohost_verifications` table
- Optional convenience: mirror latest summary onto `saved_listings`

Why this structure:

- the verification result is durable product state, not just an in-memory performance cache
- the verifier now accepts `cohostEmail` and `cohostUserId` as arguments, so the cache key is more naturally modeled outside the base listing row
- the dashboard and Auto-Apply flows need a fast current status, while support/debugging benefits from a richer per-account verification record

### Source-of-truth table

Recommended new table:

- `listing_cohost_verifications`

Suggested purpose:

- store the latest durable verification snapshot for a specific listing/co-host account pair
- support future history/audit expansion if we later decide to keep multiple attempts

Suggested columns:

- `id uuid primary key`
- `saved_listing_id uuid not null references saved_listings(id)`
- `airbnb_listing_id text not null`
- `cohost_user_id text not null`
- `cohost_email text not null`
- `status text not null`
- `has_full_access boolean not null default false`
- `permissions_label text`
- `payouts_label text`
- `primary_host_label text`
- `verification_method text`
- `error_code text`
- `error_message text`
- `last_checked_at timestamptz`
- `verified_at timestamptz`
- `raw_details jsonb`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`

Suggested uniqueness rule:

- unique on `saved_listing_id, airbnb_listing_id, cohost_user_id`

This gives us one current cached verification record per listing/co-host account pair.

### Mirrored summary on saved_listings

Keep a lightweight mirror of the current verification summary on `saved_listings` for fast reads.

Recommended summary fields:

- `auto_apply_cohost_status`
- `auto_apply_cohost_verified_at`
- `auto_apply_cohost_last_checked_at`

Purpose of each field:

- `auto_apply_cohost_status`
  - the current product-facing status used by the dashboard and Auto-Apply gating
- `auto_apply_cohost_verified_at`
  - when the listing was last positively confirmed to have full access
- `auto_apply_cohost_last_checked_at`
  - when any verification attempt last ran, regardless of success or failure

Why keep this mirror:

- the dashboard already reads `saved_listings`, so it can show co-host state without joining into a second table on every request
- Auto-Apply gating can do a cheap first-pass read from `saved_listings`
- the detailed table can still hold richer metadata such as permissions labels, payouts labels, primary-host labels, and raw details

### Optional details payload

The dedicated table should also store the most recent raw verification snapshot for auditability.

Suggested `raw_details` contents:

```json
{
  "pageUrl": "...",
  "targetEmail": "ashway14721@gmail.com",
  "targetUserId": "584480104",
  "permissionsLabel": "Full access",
  "payoutsLabel": "Not set up",
  "primaryHostLabel": "No",
  "hasFullAccess": true
}
```

Note:

- the dedicated table is the source of truth for per-account verification data
- the mirrored fields on `saved_listings` are an optimization for common app reads
- frequency/TTL rules for refreshing this cache will be decided later

This is optional but strongly recommended because it makes support and debugging much easier.

## API and UI Contract

The route response should include the observed state, not only the status.

Suggested response shape:

```json
{
  "cohostStatus": "verified",
  "cohostConfirmedAt": "2026-04-23T12:34:00.000Z",
  "cohostVerifiedAt": "2026-04-23T12:34:56.000Z",
  "cohostVerificationError": null,
  "cohostVerificationMethod": "browser_session",
  "cohostVerificationDetails": {
    "targetEmail": "ashway14721@gmail.com",
    "targetUserId": "584480104",
    "permissionsLabel": "Full access",
    "payoutsLabel": "Not set up",
    "primaryHostLabel": "No",
    "hasFullAccess": true
  }
}
```

Dashboard behavior can stay mostly unchanged for the first implementation:

- `verified` unlocks Auto-Apply
- any non-verified status keeps Auto-Apply blocked
- future UI can show:
  - `Permissions: Full access`
  - `Payouts: Not set up`
  - `Primary Host: No`

## Implementation Plan

### Milestone 1

First implementation milestone:

- create a demo script named `cohost_full_access_verification_testing.py`
- script arguments include:
  - a list of Airbnb listing ids
  - co-host email argument, default `ashway14721@gmail.com`
  - co-host Airbnb user id argument, default `584480104`
- when executed, the script runs `verify_cohost_access()` for each listing id
- for each verification result, the script writes the cache into:
  - `listing_cohost_verifications`
  - `saved_listings`

Milestone 1 purpose:

- prove the end-to-end verification flow before wiring it into the dashboard runtime path
- validate the browser-session verifier against multiple real listings
- establish the durable cache model in Supabase first

Milestone 1 scope:

- create the new Supabase table `listing_cohost_verifications`
- add the mirrored summary cache fields needed on `saved_listings`
- implement the demo/test script
- upsert one current verification record per listing/co-host pair
- mirror the latest summary into the corresponding `saved_listings` row

Milestone 1 non-goals:

- deciding the long-term automatic refresh frequency
- triggering verification on every dashboard load
- finalizing the production API orchestration path

### Phase 1 after Milestone 1

- add worker/browser utility to verify the co-host detail page
- wire `src/lib/cohostVerification.ts` to the real checker
- update `POST /api/listings/[id]/cohost-verify`
- optionally add `auto_apply_cohost_verification_details`
- keep dashboard UI behavior unchanged except for richer response data

### Phase 2

- surface the last observed `Permissions`, `Payouts`, and `Primary Host` values in the dashboard
- add retry guidance for auth-expired vs co-host-missing vs insufficient-permission failures
- add metrics/logging for verification success rate

## Exact Files Expected To Change In Implementation

- `src/lib/cohostVerification.ts`
- `src/app/api/listings/[id]/cohost-verify/route.ts`
- `src/lib/schemas.ts`
- `src/lib/listing.ts`
- `supabase/migrations/...` for optional verification details JSON column
- `worker/...` new verifier module and any worker integration point needed for CDP browser access

## Open Decisions For Review

1. Verification target
   Accept both `cohostEmail` and `cohostUserId` as arguments.
   Use `cohostUserId` as the primary lookup key for the Airbnb detail page.
   Use `cohostEmail` as supporting metadata and future-proofing for account swaps.

2. Verification rule
   Treat `Permissions = Full access` as the only condition that marks the listing `verified`.

3. Payouts and Primary Host
   Capture and return these values for visibility, but do not block verification on them unless product rules change later.

4. Runtime model
   Prefer the local authenticated browser/CDP path over a server-only route because the Airbnb host page depends on a real logged-in session.

## Recommendation

Approve implementation using the browser-session verifier with:

- argument-driven co-host identity:
  - `cohostEmail`
  - `cohostUserId`
- success rule `Permissions === Full access`
- `Payouts` and `Primary Host` captured as metadata
- existing `verified` / `verification_failed` status model preserved

This matches the current architecture best and is the most realistic way to verify the private Airbnb hosting page without overstating what the system can reliably know.
