# Airahost Co-Host Feature Discussion

## Goal

Add a feature in the user dashboard so each saved listing can offer an `Add Airahost as Co-host` action that opens the corresponding Airbnb co-host invite page for that listing.

## Product Intent

- The feature lives in the dashboard's `Saved Listings` area.
- For each listing, Airahost should help the user jump directly to Airbnb's co-host invite flow.
- The user will log into Airbnb and complete two-factor verification in Airbnb's own UI.
- Airahost should also surface the Airahost co-host email so the user can easily use it during the invite flow.

## Key Airbnb URL Pattern

The Airbnb co-host invite URL pattern is:

```text
https://www.airbnb.com/hosting/listings/editor/{listing_id}/details/co-hosts/invite
```

Example:

```text
https://www.airbnb.com/hosting/listings/editor/1252737133905911173/details/co-hosts/invite
```

The `listing_id` is the Airbnb room/listing ID, such as the number shown in labels like:

```text
Airbnb Listing #1305899249107196055
```

## Findings From Code Review

- The dashboard saved listings rail is rendered in `/Users/lambulandllc/Projects/Aira/airahost/src/app/dashboard/page.tsx`.
- Listing cards are rendered in `/Users/lambulandllc/Projects/Aira/airahost/src/components/dashboard/ListingCard.tsx`.
- Saved listings are fetched from `/Users/lambulandllc/Projects/Aira/airahost/src/app/api/listings/route.ts`.
- Listings created through the Airbnb URL flow already persist Airbnb URL information in saved listing `input_attributes`.
- The URL is typically stored as `input_attributes.listingUrl`, and some legacy data may use `input_attributes.listing_url`.
- Listings created by criteria instead of Airbnb URL may not have a stored Airbnb listing URL or room ID.

## Confirmed Product Decisions

1. We are confident to get the listing ID when a saved listing has an Airbnb URL.
2. If a listing has no Airbnb listing ID, the button should be blank or disabled.
3. If the listing was created by criteria instead of Airbnb URL, the button should be blank or disabled.
4. The Airbnb co-host invite page should open in a new tab.
5. The feature should include helper UX around the Airahost email and next steps.

## Recommended UX Design

### Main Action

Add a button on each eligible saved listing:

```text
Add Airahost as Co-host
```

Behavior:

- If an Airbnb room ID can be derived, the button is enabled.
- Clicking it opens the Airbnb co-host invite URL in a new tab.
- The new tab is where the user logs into Airbnb and completes 2FA.

Fallback behavior:

- If no Airbnb room ID can be derived, the button should be disabled or omitted.
- Preferred helper text:

```text
Airbnb listing URL required
```

### Airahost Email

Store the Airahost email as a global public config value:

```text
NEXT_PUBLIC_AIRAHOST_COHOST_EMAIL
```

Recommended UI:

- Show the Airahost email near the co-host button.
- Include a one-click `Copy Airahost email` helper if configured.
- If the env var is missing, fail gracefully and hide the email helper UI.

### Hover / Popover Design

Do not use a long plain HTML `title` tooltip for feature explanation.

Recommended design:

- Keep the main button label short.
- Add a small info icon beside the button.
- On hover, focus, or click, show a compact popover.

Why this is better:

- It scales as more Airahost features are added.
- It works better on both desktop and mobile.
- It keeps the button itself clean and readable.

Recommended content model:

- Title
- Short intro sentence
- List of benefits from a reusable config array

Initial benefit list:

- `Auto-manage pricing without manual updates`
- `Auto-respond to guest questions`
- `More Airahost co-hosting features coming soon`

Recommended wording:

- Title: `What Airahost helps with`
- Intro: `After you add Airahost as co-host, we can help with:`

## Important Technical Constraint

Auto-filling the Airbnb co-host email field from the AiraHost web app is **not** robustly possible through a normal website flow.

Reason:

- Airbnb is on a different domain/origin.
- Browser cross-origin protections prevent the AiraHost web app from controlling Airbnb's DOM in another tab.
- That means the AiraHost site cannot reliably auto-fill Airbnb's invite modal after opening it.

This also means:

- Do not attempt cross-tab DOM scripting from the web app.
- Do not attempt to automate Airbnb login.
- Do not attempt to bypass Airbnb 2FA.

## Robust Path For Auto-Fill

If true auto-fill of the Airbnb invite modal is a hard requirement, the recommended solution is:

### Chrome Extension

This is the only robust path discussed for auto-filling the Airbnb co-host email field after the user logs in and completes 2FA.

Why:

- A Chrome extension can run on Airbnb pages directly.
- It can detect the co-host invite page/modal.
- It can fill the configured Airahost email into the Airbnb form.

Recommended scope split:

- V1: Dashboard button + new tab handoff + email copy helper + benefit popover
- V2: Chrome extension for robust in-Airbnb auto-fill

Not recommended for this product flow:

- Server-side Playwright automation for the user's live Airbnb session
- Cross-origin scripting from the main web app
- Heavy local automation flows for normal users

## Verification Test Script Usage

For engineering validation, the repo also includes:

```text
worker/cohost_full_access_verification_testing.py
```

This script verifies whether the Airahost co-host account has `Full access` on a target Airbnb listing by opening the Airbnb hosting co-host detail page through a locally running Chrome session attached over CDP.

Recommended invocation:

```bash
python -m worker.cohost_full_access_verification_testing \
  --listing-id 1596737613274892756
```

Runtime requirements:

- Chrome must be started with a remote debugging port.
- Default CDP endpoint is `http://127.0.0.1:9222`.
- The script expects a live Airbnb Hosting session inside that CDP Chrome profile.
- The worker environment must still provide `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.

Example macOS launch command:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-cdp-profile"
```

Login requirement:

- Log into Airbnb in that Chrome profile before running the script.
- Log in as the co-host account being verified.
- For the current default test configuration, that means the Airbnb account matching `cohost_user_id = 584480104`.

Why this matters:

- The script builds a URL in the form:
  `https://www.airbnb.com/hosting/listings/editor/{listing_id}/details/co-hosts/{cohost_user_id}`
- Airbnb permission checks are evaluated in the context of the logged-in browser session.
- If the CDP browser is not logged in, the script should return an auth-required verification failure instead of a valid permission result.

Port guidance:

- Port `9222` is only the default, not a hard requirement.
- If another port is used, pass `--cdp-url http://127.0.0.1:{port}` or set `CDP_URL` accordingly.

## Co-Host Verification Data Model

The co-host verification flow uses both `saved_listings` and the newer `listing_cohost_verifications` cache table, with migrations `020` and `022` playing different roles.

### `saved_listings`

`saved_listings` remains the lightweight summary table used by the dashboard and by Auto-Apply gating logic.

Its co-host fields represent the current mirrored state for a listing, such as:

- `auto_apply_cohost_status`
- `auto_apply_cohost_verified_at`
- `auto_apply_cohost_last_checked_at`
- `auto_apply_cohost_verification_method`
- `auto_apply_cohost_verification_error`

This table is not intended to store the full detailed verification snapshot history.

### Migration `020_cohost_verification_model.sql`

Migration `020` introduces the co-host verification state model on `saved_listings`.

Its main job is to replace the old single boolean approach with a clearer state machine and supporting summary fields. It adds fields such as:

- `auto_apply_cohost_status`
- `auto_apply_cohost_confirmed_at`
- `auto_apply_cohost_verified_at`
- `auto_apply_cohost_verification_error`
- `auto_apply_cohost_verification_method`

It also migrates older `auto_apply_cohost_ready = true` rows into `auto_apply_cohost_status = 'user_confirmed'`.

### Migration `022_listing_cohost_verifications.sql`

Migration `022` adds the dedicated `listing_cohost_verifications` table and one additional summary mirror timestamp on `saved_listings`.

Its main job is to establish the durable source-of-truth verification cache for one listing plus one co-host account pair, including fields such as:

- `status`
- `has_full_access`
- `permissions_label`
- `payouts_label`
- `primary_host_label`
- `verification_method`
- `error_code`
- `error_message`
- `last_checked_at`
- `verified_at`
- `raw_details`

It also adds `auto_apply_cohost_last_checked_at` to `saved_listings` so the latest verification attempt time is cheaply available for dashboard reads.

### Relationship Between `saved_listings` and `listing_cohost_verifications`

Recommended mental model:

- `listing_cohost_verifications` is the detailed source-of-truth cache row for the verification result.
- `saved_listings` is the lightweight mirrored summary used by the product UI and Auto-Apply gating.

In other words:

- `020` defines the summary state model on `saved_listings`.
- `022` adds the detailed verification table and keeps `saved_listings` as the fast summary mirror.

These migrations are complementary, not duplicates. Some `saved_listings` columns appear in both migrations with `IF NOT EXISTS`, but the overall responsibilities are different and both migrations are needed for the full co-host verification design.

## Strong Implementation Prompt

```text
Implement a new “Add Airahost as Co-host” feature in the AiraHost Next.js dashboard, with a scalable benefit popover and graceful fallback behavior.

Project context:
- Saved listings rail is rendered in `src/app/dashboard/page.tsx`
- Each listing card is rendered by `src/components/dashboard/ListingCard.tsx`
- Listings are fetched from `src/app/api/listings/route.ts`
- Saved listings may contain Airbnb URL data inside `input_attributes.listingUrl`
- Some legacy rows may use `input_attributes.listing_url`
- Listings created from criteria instead of Airbnb URL may not have any Airbnb room ID
- The app is a Next.js frontend with TypeScript

Feature goal:
On each saved listing card, add a button that helps the user open the Airbnb co-host invite page for that specific Airbnb listing so they can add Airahost as co-host.

Primary behavior:
- Add a button labeled `Add Airahost as Co-host`
- Derive the Airbnb room/listing ID from `input_attributes.listingUrl` or legacy `input_attributes.listing_url`
- Extract the numeric room ID from Airbnb URLs like `/rooms/{id}`
- Build the Airbnb co-host invite URL in this exact format:
  `https://www.airbnb.com/hosting/listings/editor/{listing_id}/details/co-hosts/invite`
- On click, open the URL in a new browser tab with appropriate safe link attributes
- The new tab is where the user logs into Airbnb and completes 2FA in their own Airbnb session

Behavior for missing listing ID:
- If a listing does not have a derivable Airbnb room ID, do not allow the action
- Either hide the button entirely or render it in a disabled state
- Prefer a disabled state with clear helper text such as:
  `Airbnb listing URL required`
- This includes listings created by criteria instead of Airbnb URL

Global Airahost email:
- Add a global public config value:
  `NEXT_PUBLIC_AIRAHOST_COHOST_EMAIL`
- Use it to show a helper action near the co-host button:
  - either `Copy Airahost email`
  - or a compact inline display of the email plus a copy button
- If the env var is missing, fail gracefully and omit the email helper UI

Scalable hover/popover design:
Do not use a long plain HTML title tooltip for feature explanation. Instead, design a small reusable info popover or tooltip-triggered card next to the button.

Requirements for this helper UI:
- Add a small info icon beside the co-host button
- On hover and click/focus, show a compact popover panel
- The popover content must come from a reusable array/constant so future benefits can be added without changing rendering logic
- Example structure:
  - title
  - short intro sentence
  - list of benefits
- Initial benefit content should be:
  - `Auto-manage pricing without manual updates`
  - `Auto-respond to guest questions`
  - `More Airahost co-hosting features coming soon`
- The popover should work reasonably on desktop and mobile
- Keep the design visually consistent with the existing dashboard card UI
- Prefer short, clear copy over large paragraphs

Type safety:
- Update the listing-related frontend types so `input_attributes` can safely include:
  - `listingUrl?: string | null`
  - `listing_url?: string | null`
- Avoid relying on broad `Record<string, unknown>` access when a specific type can be used
- Add a small utility function to extract Airbnb room IDs safely and defensively

UX expectations:
- Enabled listing:
  - show co-host button
  - show info icon/popover
  - show email copy helper if configured
- Disabled listing:
  - do not open anything
  - show disabled or blank state with clear explanation
- Clicking the main button should not attempt any cross-site DOM automation from the web app

Important architectural constraint:
- A normal web app cannot robustly auto-fill Airbnb’s invite modal because Airbnb is a different origin/domain and browser cross-origin protections prevent our site from controlling that page’s DOM
- Therefore, do not implement cross-tab or cross-origin DOM scripting from the AiraHost web app
- Do not attempt to automate Airbnb login or bypass 2FA

Chrome extension recommendation:
- In comments or implementation notes, explicitly document that a Chrome extension is the only robust path for auto-filling the Airbnb co-host email field after the user logs in and completes 2FA
- Treat the Chrome extension as a future V2 path
- Do not build the extension in this task
- Do not use Playwright or server-side automation for this feature

Acceptance criteria:
- Listings with a valid Airbnb room ID show an enabled `Add Airahost as Co-host` button
- Clicking the button opens the correct Airbnb co-host invite URL in a new tab
- Listings without a valid Airbnb room ID show a disabled or blank fallback state
- The Airahost email is configurable via `NEXT_PUBLIC_AIRAHOST_COHOST_EMAIL`
- A reusable info popover explains what Airahost helps with and is easy to extend
- TypeScript types are improved for listing URL access
- No existing dashboard functionality regresses
- No cross-origin auto-fill is attempted in the web app
```

## Shareable Summary

The team aligned on a V1 flow where dashboard users can open the Airbnb co-host invite page in a new tab for listings that have a derivable Airbnb listing ID. The UI should include a scalable benefits popover and an Airahost email helper. Listings without an Airbnb ID should show a disabled or blank fallback state. Robust auto-fill of Airbnb's co-host email field is not feasible from the main AiraHost web app because of browser cross-origin protections, so a Chrome extension is the recommended V2 path if auto-fill remains a hard requirement.
