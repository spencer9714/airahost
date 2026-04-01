from __future__ import annotations

import argparse
import json
import os
from typing import Any

from ml.data import (
    fetch_comparable_pool_entries,
    fetch_saved_listing_by_id,
    fetch_saved_listing_by_url,
)
from ml.supabase_client import get_client


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read saved listing and comparable data from Supabase for ML testing."
    )

    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--saved-listing-id", help="UUID of a saved listing in Supabase")
    group.add_argument("--listing-url", help="Airbnb listing URL stored in saved_listings.input_attributes")

    parser.add_argument(
        "--comparable-limit",
        type=int,
        default=20,
        help="Max number of comparable pool entries to load",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON for saved listing and comparable rows",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = get_client()

    saved_listing = None
    if args.saved_listing_id:
        saved_listing = fetch_saved_listing_by_id(client, args.saved_listing_id)
        if not saved_listing:
            raise ValueError(f"Saved listing not found for id: {args.saved_listing_id}")

    if args.listing_url:
        saved_listing = fetch_saved_listing_by_url(client, args.listing_url)
        if not saved_listing:
            raise ValueError(f"Saved listing not found for URL: {args.listing_url}")

    if not saved_listing:
        default_id = os.getenv("ML_DEFAULT_SAVED_LISTING_ID")
        if default_id:
            print(f"Using default saved listing id from ml/.env: {default_id}")
            saved_listing = fetch_saved_listing_by_id(client, default_id)
            if not saved_listing:
                raise ValueError(
                    f"Default saved listing id {default_id} from ml/.env was not found in Supabase."
                )

    if not saved_listing:
        raise ValueError(
            "No saved listing specified and no default is configured. "
            "Set --listing-url, --saved-listing-id, or ML_DEFAULT_SAVED_LISTING_ID in ml/.env."
        )

    print("=== Saved listing ===")
    print_json(saved_listing)

    comparables = fetch_comparable_pool_entries(
        client,
        saved_listing_id=saved_listing["id"],
        limit=args.comparable_limit,
    )

    print(f"\n=== Comparable pool entries ({len(comparables)}) ===")
    if args.json:
        print_json(comparables.to_dict(orient="records"))
    else:
        for idx, row in comparables.head(args.comparable_limit).iterrows():
            print(
                f"[{idx}] id={row.get('id')} airbnb_listing_id={row.get('airbnb_listing_id')} "
                f"listing_url={row.get('listing_url')} last_nightly_price={row.get('last_nightly_price')} "
                f"similarity_score={row.get('similarity_score')}"
            )


if __name__ == "__main__":
    main()
