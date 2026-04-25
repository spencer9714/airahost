"""
Demo script for Milestone 1 co-host full-access verification.

Usage examples:
    python -m worker.cohost_full_access_verification_testing \
      --listing-id 1596737613274892756

    python -m worker.cohost_full_access_verification_testing \
      --listing-id 1596737613274892756 \
      --listing-id 1252737133905911173 \
      --cohost-email ashway14721@gmail.com \
      --cohost-user-id 584480104

This script:
1. Verifies co-host access for each Airbnb listing id through a logged-in CDP
   browser session.
2. Upserts the result into listing_cohost_verifications.
3. Mirrors the latest summary into saved_listings.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

# Load .env from worker directory first, then repo root.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)
load_dotenv(override=False)


DEFAULT_COHOST_EMAIL = "ashway14721@gmail.com"
DEFAULT_COHOST_USER_ID = "584480104"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logger = logging.getLogger("worker.cohost_verification_testing")


@dataclass
class VerificationResult:
    listing_id: str
    cohost_email: str
    cohost_user_id: str
    page_url: str
    status: str
    has_full_access: bool
    permissions_label: Optional[str]
    payouts_label: Optional[str]
    primary_host_label: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    method: str
    last_checked_at: str
    verified_at: Optional[str]
    final_url: str
    raw_text_excerpt: Optional[str]


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        force=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Airbnb co-host access for listing ids and sync cache into Supabase."
    )
    parser.add_argument(
        "--listing-id",
        action="append",
        default=[],
        help="Airbnb listing id to verify. Repeat the flag for multiple listings.",
    )
    parser.add_argument(
        "--listing-ids",
        default="",
        help="Optional comma-separated list of Airbnb listing ids.",
    )
    parser.add_argument(
        "--cohost-email",
        default=DEFAULT_COHOST_EMAIL,
        help=f"Cohost email metadata. Default: {DEFAULT_COHOST_EMAIL}",
    )
    parser.add_argument(
        "--cohost-user-id",
        default=DEFAULT_COHOST_USER_ID,
        help=f"Cohost Airbnb user id. Default: {DEFAULT_COHOST_USER_ID}",
    )
    parser.add_argument(
        "--cdp-url",
        default=os.getenv("CDP_URL", DEFAULT_CDP_URL),
        help="Chrome DevTools Protocol URL. Defaults to env CDP_URL or http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=45000,
        help="Navigation timeout in milliseconds. Default: 45000",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run verification without writing results into Supabase.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def normalize_listing_ids(values: Iterable[str], csv_values: str) -> List[str]:
    listing_ids: List[str] = []
    seen: set[str] = set()

    for raw in list(values) + csv_values.split(","):
        candidate = str(raw).strip()
        if not candidate:
            continue
        if not re.fullmatch(r"\d+", candidate):
            raise ValueError(f"Invalid Airbnb listing id: {candidate}")
        if candidate not in seen:
            seen.add(candidate)
            listing_ids.append(candidate)

    if not listing_ids:
        raise ValueError("Provide at least one Airbnb listing id via --listing-id or --listing-ids.")

    return listing_ids


def extract_airbnb_listing_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"(?:/rooms/|/listings/)(?:plus/)?(\d+)", url)
    if not match:
        return None
    return match.group(1)


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    compact = re.sub(r"\s+", " ", value).strip()
    return compact or None


def extract_section_value(body_text: str, label: str, next_labels: List[str]) -> Optional[str]:
    start = body_text.find(label)
    if start < 0:
        return None

    remaining = body_text[start + len(label) :]
    end_positions = []
    for next_label in next_labels:
        idx = remaining.find(next_label)
        if idx >= 0:
            end_positions.append(idx)
    if end_positions:
        remaining = remaining[: min(end_positions)]

    lines = [clean_text(line) for line in remaining.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None

    for line in lines:
        lowered = line.lower()
        if lowered == label.lower():
            continue
        if lowered.startswith("edit ") or lowered.startswith("share "):
            continue
        return line
    return None


def build_page_url(listing_id: str, cohost_user_id: str) -> str:
    return (
        "https://www.airbnb.com/hosting/listings/editor/"
        f"{listing_id}/details/co-hosts/{cohost_user_id}"
    )


def verify_cohost_access(
    *,
    listing_id: str,
    cohost_email: str,
    cohost_user_id: str,
    cdp_url: str,
    timeout_ms: int,
) -> VerificationResult:
    from playwright.sync_api import sync_playwright

    page_url = build_page_url(listing_id, cohost_user_id)
    checked_at = datetime.now(timezone.utc).isoformat()

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
        had_contexts = bool(browser.contexts)
        context = browser.contexts[0] if had_contexts else browser.new_context()
        created_context = None if had_contexts else context
        page = context.new_page()

        try:
            logger.info("Verifying listing %s via %s", listing_id, page_url)
            response = page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                # Airbnb pages often keep background requests active; DOM content is enough for the demo.
                pass
            page.wait_for_timeout(1500)

            final_url = str(page.url or "")
            body_text = page.locator("body").inner_text(timeout=5000)
            body_text = body_text or ""
            excerpt = clean_text(body_text[:800])
            normalized_text = body_text.lower()

            if "login" in final_url.lower() or "log in" in normalized_text:
                return VerificationResult(
                    listing_id=listing_id,
                    cohost_email=cohost_email,
                    cohost_user_id=cohost_user_id,
                    page_url=page_url,
                    status="verification_failed",
                    has_full_access=False,
                    permissions_label=None,
                    payouts_label=None,
                    primary_host_label=None,
                    error_code="browser_auth_required",
                    error_message="The CDP browser is not logged into Airbnb hosting.",
                    method="browser_session",
                    last_checked_at=checked_at,
                    verified_at=None,
                    final_url=final_url,
                    raw_text_excerpt=excerpt,
                )

            if (
                f"/details/co-hosts/{cohost_user_id}" not in final_url
                and "permissions" not in normalized_text
            ):
                return VerificationResult(
                    listing_id=listing_id,
                    cohost_email=cohost_email,
                    cohost_user_id=cohost_user_id,
                    page_url=page_url,
                    status="verification_failed",
                    has_full_access=False,
                    permissions_label=None,
                    payouts_label=None,
                    primary_host_label=None,
                    error_code="cohost_not_found",
                    error_message="The target Airbnb co-host detail page was not available for this listing.",
                    method="browser_session",
                    last_checked_at=checked_at,
                    verified_at=None,
                    final_url=final_url,
                    raw_text_excerpt=excerpt,
                )

            permissions_label = extract_section_value(
                body_text,
                "Permissions",
                ["Payouts", "Primary Host"],
            )
            payouts_label = extract_section_value(
                body_text,
                "Payouts",
                ["Primary Host"],
            )
            primary_host_label = extract_section_value(
                body_text,
                "Primary Host",
                [],
            )

            if not permissions_label:
                return VerificationResult(
                    listing_id=listing_id,
                    cohost_email=cohost_email,
                    cohost_user_id=cohost_user_id,
                    page_url=page_url,
                    status="verification_failed",
                    has_full_access=False,
                    permissions_label=None,
                    payouts_label=payouts_label,
                    primary_host_label=primary_host_label,
                    error_code="page_parse_failed",
                    error_message="Could not extract the Permissions value from the Airbnb co-host page.",
                    method="browser_session",
                    last_checked_at=checked_at,
                    verified_at=None,
                    final_url=final_url,
                    raw_text_excerpt=excerpt,
                )

            has_full_access = permissions_label.strip().lower() == "full access"
            if has_full_access:
                return VerificationResult(
                    listing_id=listing_id,
                    cohost_email=cohost_email,
                    cohost_user_id=cohost_user_id,
                    page_url=page_url,
                    status="verified",
                    has_full_access=True,
                    permissions_label=permissions_label,
                    payouts_label=payouts_label,
                    primary_host_label=primary_host_label,
                    error_code=None,
                    error_message=None,
                    method="browser_session",
                    last_checked_at=checked_at,
                    verified_at=checked_at,
                    final_url=final_url,
                    raw_text_excerpt=excerpt,
                )

            return VerificationResult(
                listing_id=listing_id,
                cohost_email=cohost_email,
                cohost_user_id=cohost_user_id,
                page_url=page_url,
                status="verification_failed",
                has_full_access=False,
                permissions_label=permissions_label,
                payouts_label=payouts_label,
                primary_host_label=primary_host_label,
                error_code="permissions_not_full_access",
                error_message=f"Permissions are '{permissions_label}', not 'Full access'.",
                method="browser_session",
                last_checked_at=checked_at,
                verified_at=None,
                final_url=final_url,
                raw_text_excerpt=excerpt,
            )
        finally:
            try:
                page.close()
            except Exception:
                pass
            if created_context is not None:
                try:
                    created_context.close()
                except Exception:
                    pass


def fetch_saved_listings_by_airbnb_id(client: Any) -> Dict[str, List[Dict[str, Any]]]:
    result = (
        client.table("saved_listings")
        .select("id, user_id, name, input_attributes, auto_apply_cohost_verified_at")
        .execute()
    )
    listings = result.data or []
    by_airbnb_id: Dict[str, List[Dict[str, Any]]] = {}

    for listing in listings:
        attrs = listing.get("input_attributes") or {}
        listing_url = attrs.get("listingUrl") or attrs.get("listing_url")
        airbnb_listing_id = extract_airbnb_listing_id_from_url(listing_url)
        if not airbnb_listing_id:
            continue
        by_airbnb_id.setdefault(airbnb_listing_id, []).append(listing)

    return by_airbnb_id


def build_raw_details(result: VerificationResult) -> Dict[str, Any]:
    return {
        "pageUrl": result.page_url,
        "finalUrl": result.final_url,
        "targetEmail": result.cohost_email,
        "targetUserId": result.cohost_user_id,
        "permissionsLabel": result.permissions_label,
        "payoutsLabel": result.payouts_label,
        "primaryHostLabel": result.primary_host_label,
        "hasFullAccess": result.has_full_access,
        "errorCode": result.error_code,
        "errorMessage": result.error_message,
        "rawTextExcerpt": result.raw_text_excerpt,
    }


def upsert_verification_cache(
    *,
    client: Any,
    listing_row: Dict[str, Any],
    result: VerificationResult,
) -> None:
    existing_verified_at = listing_row.get("auto_apply_cohost_verified_at")
    verified_at = result.verified_at or existing_verified_at

    verification_row = {
        "saved_listing_id": listing_row["id"],
        "user_id": listing_row["user_id"],
        "airbnb_listing_id": result.listing_id,
        "cohost_user_id": result.cohost_user_id,
        "cohost_email": result.cohost_email,
        "status": result.status,
        "has_full_access": result.has_full_access,
        "permissions_label": result.permissions_label,
        "payouts_label": result.payouts_label,
        "primary_host_label": result.primary_host_label,
        "verification_method": result.method,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "last_checked_at": result.last_checked_at,
        "verified_at": verified_at,
        "raw_details": build_raw_details(result),
    }

    client.table("listing_cohost_verifications").upsert(
        verification_row,
        on_conflict="saved_listing_id,airbnb_listing_id,cohost_user_id",
    ).execute()

    saved_listing_update = {
        "auto_apply_cohost_status": result.status,
        "auto_apply_cohost_verified_at": verified_at,
        "auto_apply_cohost_last_checked_at": result.last_checked_at,
        "auto_apply_cohost_verification_method": result.method,
        "auto_apply_cohost_verification_error": result.error_message,
    }
    client.table("saved_listings").update(saved_listing_update).eq("id", listing_row["id"]).execute()


def print_result_summary(result: VerificationResult, matched_rows: List[Dict[str, Any]]) -> None:
    logger.info(
        "Listing %s -> status=%s full_access=%s permissions=%s payouts=%s primary_host=%s matched_saved_listings=%s",
        result.listing_id,
        result.status,
        result.has_full_access,
        result.permissions_label,
        result.payouts_label,
        result.primary_host_label,
        len(matched_rows),
    )
    if result.error_message:
        logger.warning("Listing %s error: %s", result.listing_id, result.error_message)


def get_supabase_client() -> Any:
    """Support both `python -m worker...` and `python worker/...py` execution."""
    try:
        from worker.core.db import get_client
    except ModuleNotFoundError as exc:
        if exc.name != "worker":
            raise
        from core.db import get_client

    return get_client()


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    try:
        listing_ids = normalize_listing_ids(args.listing_id, args.listing_ids)
    except ValueError as exc:
        logger.error(str(exc))
        return 2

    try:
        client = get_supabase_client()
    except Exception as exc:
        logger.error("Supabase client initialization failed: %s", exc)
        return 2

    saved_listings_by_airbnb_id = fetch_saved_listings_by_airbnb_id(client)

    failures = 0
    for listing_id in listing_ids:
        try:
            result = verify_cohost_access(
                listing_id=listing_id,
                cohost_email=args.cohost_email,
                cohost_user_id=str(args.cohost_user_id).strip(),
                cdp_url=str(args.cdp_url).strip(),
                timeout_ms=args.timeout_ms,
            )
        except Exception as exc:
            failures += 1
            logger.exception("Verification crashed for listing %s: %s", listing_id, exc)
            continue

        matched_rows = saved_listings_by_airbnb_id.get(listing_id, [])
        print_result_summary(result, matched_rows)

        if args.dry_run:
            logger.info("Dry run enabled; skipping database writes for listing %s.", listing_id)
            continue

        if not matched_rows:
            failures += 1
            logger.warning(
                "No saved_listings row matched Airbnb listing id %s. Verification result was not persisted.",
                listing_id,
            )
            continue

        for listing_row in matched_rows:
            try:
                upsert_verification_cache(client=client, listing_row=listing_row, result=result)
                logger.info(
                    "Persisted verification cache for saved_listing_id=%s (%s).",
                    listing_row["id"],
                    listing_row.get("name") or "Listing",
                )
            except Exception as exc:
                failures += 1
                logger.exception(
                    "Failed to persist verification cache for saved_listing_id=%s: %s",
                    listing_row["id"],
                    exc,
                )

    if failures:
        logger.error("Completed with %s failure(s).", failures)
        return 1

    logger.info("Completed successfully for %s listing(s).", len(listing_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
