# BUG_TASKS.md — AiraHost

Use this file to track scraping and analysis failures that need investigation.
It is optimized for AI or human contributors to take one issue at a time,
reproduce it, fix it, and leave clear notes for the next session.

## How To Use

1. Add a new bug entry as soon as a failure is observed.
2. Keep one bug = one task. Do not group unrelated failures together.
3. Move the status as work progresses:
   - `open`
   - `investigating`
   - `blocked`
   - `fix_ready`
   - `done`
4. When an agent starts work, assign the bug to that session and fill in the
   reproduction notes before changing code.
5. After a fix, record:
   - root cause
   - files changed
   - how the fix was verified
   - any remaining risk

## Prioritization

- `P0`: blocks report generation or causes wrong pricing output
- `P1`: common failure with a workable manual fallback
- `P2`: edge case, partial degradation, or weak diagnostics

## Entry Template

Copy this block for each new issue.

```md
## BUG-XXX - Short title

- Status: `open`
- Priority: `P1`
- Owner: `unassigned`
- Area: `worker/scraper`
- First seen: `YYYY-MM-DD`
- Input mode: `criteria` | `listing_url`
- Environment: `local worker + Airbnb logged-in session`

### Symptoms
- What failed?
- What did the user or logs show?

### Reproduction
- Report ID:
- Listing URL / search criteria:
- Expected behavior:
- Actual behavior:
- Log snippets:

### Investigation Notes
- Hypotheses:
- Observations:
- Suspected files:

### Resolution
- Root cause:
- Fix summary:
- Files changed:
- Verification:
- Follow-up:
```

## Active Queue

## BUG-001 - Criteria-based analysis fails for some searches

- Status: `fix_ready`
- Priority: `P0`
- Owner: `Codex session 2026-04-08`
- Area: `worker/scraper`
- First seen: `2026-04-08`
- Input mode: `criteria`
- Environment: `local worker + Airbnb logged-in session`

### Symptoms
- Criteria-mode reports can complete successfully but show the wrong target
  listing, wrong search criteria, and wrong comparable market.
- Report transparency appears to use one of the Airbnb comparable listings as
  the target listing instead of preserving the user-entered property.
- Market location can drift to a different city entirely, causing all
  comparable listings to come from the wrong area.

### Reproduction
- Share/report URL: `https://www.airahost.com/r/nxn4fw5e`
- Report ID: unknown from UI
- Search criteria:
  - City: `Belmont`
  - State: `CA`
  - ZIP: `94002-2216`
  - Street address: `933 Holly Rd`
  - Property type: `Entire home`
  - Bedrooms: `3`
  - Bathrooms: `2`
  - Max guests: `6`
  - Start date: `2026-04-23`
  - End date: `2026-04-25`
- Expected behavior:
  - `Your listing` should reflect the user-entered Belmont property profile
  - `Search criteria used` should use Belmont and 6 guests
  - comparable listings should be near Belmont, CA
- Actual behavior:
  - `Your listing` is wrong and appears to come from one of the scraped
    comparable listings
  - `Search criteria used` shows `Charlotte` instead of `Belmont`
  - `Search criteria used` shows `8` guests instead of `6`
  - comparable listings are from Charlotte rather than Belmont
- Log snippets:
  - `2026-04-09 01:04:38 [INFO] worker.scraper: [criteria] Search URL: https://www.airbnb.com/s/Belmont%2C%20CA/homes?checkin=2026-04-21&checkout=2026-04-23&adults=6`

### Investigation Notes
- Hypotheses:
  - Airbnb search path formatting may be too aggressively URL-encoded for
    city/state searches, causing Airbnb to resolve the wrong market
  - criteria mode selects an Airbnb search-result anchor, then reuses the URL
    mode scrape path in a way that overwrites criteria-mode target metadata
  - `targetSpec` may be populated from the selected anchor listing rather than
    the user-entered criteria
  - `queryCriteria.locationBasis` and `queryCriteria.searchAdults` may be
    rebuilt from the anchor listing rather than preserved from the original
    criteria search
  - comparable collection may then run around the anchor listing location,
    propagating the wrong city into the rest of the report
- Observations:
  - UI cards read from `resultSummary.targetSpec` and
    `resultSummary.queryCriteria`, so the bad data is likely produced by the
    worker, not by the frontend display layer
  - the current `build_search_url()` implementation was encoding the entire
    location path segment, producing `Belmont%2C%20CA`
  - per user validation, Airbnb resolves the correct market when the path uses
    `Belmont,CA` instead
  - `run_criteria_search()` builds an initial criteria-based `query_criteria`
    correctly from the user inputs, then selects an anchor Airbnb listing and
    calls `run_scrape(anchor_url, ...)`
  - `run_scrape()` rebuilds both `targetSpec` and `queryCriteria` from the
    passed Airbnb listing URL, which is appropriate for URL mode but unsafe for
    criteria mode
- Suspected files:
  - `worker/scraper/comparable_collector.py`
  - `worker/scraper/price_estimator.py`
  - `worker/main.py`
  - `src/components/report/TargetSpecCard.tsx`
  - `src/components/report/QueryCriteriaCard.tsx`

### Resolution
- Root cause:
  - `build_search_url()` encoded the city/state path as a generic URL-encoded
    string (`Belmont%2C%20CA`) instead of Airbnb's expected city/state search
    path style (`Belmont,CA`), which could route the initial search to the
    wrong market
  - criteria mode correctly resolves the initial search location, but after
    anchor selection it reused the shared URL-mode `run_scrape()` pipeline
  - the shared pipeline treated the anchor listing as the target source of
    truth for `targetSpec`, `queryCriteria`, and day-query search context
  - that let a wrong anchor location or capacity overwrite the user-entered
    Belmont/6-guest criteria and redirect comparable collection to the wrong market
- Fix summary:
  - updated `build_search_url()` to normalize `City, State` as `City,State`
    and preserve the comma in the Airbnb search path
  - added a criteria-mode override path in `run_scrape()` so the anchor URL is
    retained only as a scrape seed / exclusion reference
  - preserved user-entered target metadata and criteria-mode `queryCriteria`
    during the second-pass scrape
  - disabled comparable spec repair from replacing criteria-mode target fields
    with anchor listing data
- Files changed:
  - `worker/scraper/comparable_collector.py`
  - `worker/scraper/price_estimator.py`
- Verification:
  - `python3 -m py_compile worker/scraper/price_estimator.py`
  - URL builder now emits Airbnb-style city/state paths such as `Belmont,CA`
  - full Airbnb runtime verification still pending
- Follow-up:
  - rerun the Belmont report scenario and confirm:
    - `Your listing` stays on Belmont user input
    - `Search criteria used` stays `Belmont, CA` and `6`
    - comparable listings stay near Belmont
