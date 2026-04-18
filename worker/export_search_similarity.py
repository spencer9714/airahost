from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from worker.core.similarity import similarity_score
from worker.scraper.airbnb_client import AirbnbClient
from worker.scraper.comp_collection import _map_search_row_to_spec
from worker.scraper.parsers import parse_search_listing_context, parse_search_response
from worker.scraper.parsers import parse_pdp_baths_property_type_fast, parse_pdp_response
from worker.scraper.target_extractor import (
    ListingSpec,
    map_pdp_to_listing_spec,
    normalize_property_type,
)


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _build_target(args: argparse.Namespace) -> ListingSpec:
    amenities: List[str] = []
    if args.target_amenities:
        amenities = [x.strip() for x in args.target_amenities.split(",") if x.strip()]
    return ListingSpec(
        url="",
        location=args.query,
        accommodates=_to_int(args.target_accommodates),
        bedrooms=_to_int(args.target_bedrooms),
        beds=_to_int(args.target_beds),
        baths=_to_float(args.target_baths),
        property_type=(args.target_property_type or "").strip(),
        amenities=amenities,
    )


def _default_checkout(checkin: str) -> str:
    day = datetime.strptime(checkin, "%Y-%m-%d").date()
    return (day + timedelta(days=1)).isoformat()


def _parse_offsets(raw: str) -> List[int]:
    offsets: List[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        offsets.append(int(token))
    return offsets or [0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Airbnb search listings with similarity scores to JSON."
    )
    parser.add_argument("--query", required=True, help="Search location query, e.g. 'Belmont, California'")
    parser.add_argument("--checkin", required=True, help="Check-in date (YYYY-MM-DD)")
    parser.add_argument("--checkout", default=None, help="Checkout date (YYYY-MM-DD). Defaults to checkin+1")
    parser.add_argument("--adults", type=int, required=True, help="Adults count for search")
    parser.add_argument("--items-per-grid", type=int, default=80, help="Requested itemsPerGrid")
    parser.add_argument("--offsets", default="0", help="Comma-separated offsets, e.g. '0,80,160'")
    parser.add_argument("--base-url", default="https://www.airbnb.com")
    parser.add_argument("--search-guests", default=None, help="Optional hard search filter: guests")
    parser.add_argument("--search-min-bedrooms", default=None, help="Optional hard search filter: minBedrooms")
    parser.add_argument("--search-min-beds", default=None, help="Optional hard search filter: minBeds")
    parser.add_argument("--search-min-bathrooms", default=None, help="Optional hard search filter: minBathrooms")

    parser.add_argument("--target-accommodates", default=None)
    parser.add_argument("--target-bedrooms", default=None)
    parser.add_argument("--target-beds", default=None)
    parser.add_argument("--target-baths", default=None)
    parser.add_argument("--target-property-type", default="")
    parser.add_argument(
        "--target-amenities",
        default="",
        help="Comma-separated amenities for similarity scoring",
    )

    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON file path. Defaults to worker/outputs/search_similarity_<timestamp>.json",
    )
    parser.add_argument(
        "--enrich-pdp-missing",
        action="store_true",
        help="When set, fill missing fields (location/property_type/specs) from PDP response per listing.",
    )
    args = parser.parse_args()

    checkout = args.checkout or _default_checkout(args.checkin)
    offsets = _parse_offsets(args.offsets)
    target = _build_target(args)

    client = AirbnbClient(
        {
            "AIRBNB_BASE_URL": args.base_url,
            "CHECKIN": args.checkin,
            "CHECKOUT": checkout,
            "ADULTS": args.adults,
            "QUERY": args.query,
            "LOG_RAW_PAYLOADS": False,
        }
    )

    merged_rows: Dict[str, Dict[str, Any]] = {}
    first_seen_offset: Dict[str, int] = {}
    per_offset_counts: Dict[str, int] = {}

    for offset in offsets:
        overrides: Dict[str, Any] = {
            "checkin": args.checkin,
            "checkout": checkout,
            "adults": args.adults,
            "query": args.query,
            "itemsPerGrid": args.items_per_grid,
            "itemsOffset": offset,
        }
        if args.search_guests is not None:
            overrides["guests"] = int(args.search_guests)
        if args.search_min_bedrooms is not None:
            overrides["minBedrooms"] = int(args.search_min_bedrooms)
        if args.search_min_beds is not None:
            overrides["minBeds"] = int(args.search_min_beds)
        if args.search_min_bathrooms is not None:
            overrides["minBathrooms"] = float(args.search_min_bathrooms)

        _, data = client.search_listings_with_overrides(
            overrides
        )
        ids = [str(x) for x in parse_search_response(data)]
        ctx = parse_search_listing_context(data)
        per_offset_counts[str(offset)] = len(ids)
        for lid in ids:
            if lid not in first_seen_offset:
                first_seen_offset[lid] = offset
            row = ctx.get(lid, {})
            existing = merged_rows.get(lid)
            if existing is None:
                merged_rows[lid] = row
            else:
                existing_has_price = bool(
                    (existing.get("nightly_price") or 0) > 0 or (existing.get("total_price") or 0) > 0
                )
                row_has_price = bool((row.get("nightly_price") or 0) > 0 or (row.get("total_price") or 0) > 0)
                if row_has_price and not existing_has_price:
                    merged_rows[lid] = row

    listings: List[Dict[str, Any]] = []
    pdp_enriched_count = 0
    pdp_attempted_count = 0
    pdp_structural_attempted_count = 0
    pdp_structural_updated_count = 0
    for lid, row in merged_rows.items():
        comp = _map_search_row_to_spec(lid, row, args.base_url, query_nights=1)
        # Strict PDP-only for these two fields.
        comp.baths = None
        comp.property_type = ""

        pdp_data = None
        try:
            pdp_structural_attempted_count += 1
            pdp_data = client.get_listing_details(
                str(lid),
                checkin=args.checkin,
                checkout=checkout,
                adults=args.adults,
            )
            fast = parse_pdp_baths_property_type_fast(pdp_data)
            baths = fast.get("baths")
            ptype_norm = normalize_property_type(str(fast.get("property_type") or ""))
            if isinstance(baths, (int, float)):
                comp.baths = float(baths)
            if isinstance(ptype_norm, str) and ptype_norm.strip():
                comp.property_type = ptype_norm.strip()
            if comp.baths is not None or comp.property_type:
                pdp_structural_updated_count += 1
        except Exception:
            # Keep strict PDP-only behavior: do not fallback to search values.
            pass

        needs_pdp = any(
            (
                not str(comp.location or "").strip(),
                comp.accommodates is None,
                comp.bedrooms is None,
                comp.beds is None,
            )
        )
        if args.enrich_pdp_missing and needs_pdp:
            pdp_attempted_count += 1
            try:
                if pdp_data is None:
                    pdp_data = client.get_listing_details(
                        str(lid),
                        checkin=args.checkin,
                        checkout=checkout,
                        adults=args.adults,
                    )
                parsed = parse_pdp_response(pdp_data, str(lid), args.base_url.rstrip("/"))
                pdp_spec = map_pdp_to_listing_spec(parsed, f"{args.base_url.rstrip('/')}/rooms/{lid}")
                before = (
                    comp.location,
                    comp.accommodates,
                    comp.bedrooms,
                    comp.beds,
                )
                if not str(comp.location or "").strip():
                    comp.location = str(pdp_spec.location or "")
                if comp.accommodates is None:
                    comp.accommodates = pdp_spec.accommodates
                if comp.bedrooms is None:
                    comp.bedrooms = pdp_spec.bedrooms
                if comp.beds is None:
                    comp.beds = pdp_spec.beds
                after = (
                    comp.location,
                    comp.accommodates,
                    comp.bedrooms,
                    comp.beds,
                )
                if after != before:
                    pdp_enriched_count += 1
            except Exception:
                # Best effort enrichment; keep search-card data on any PDP failure.
                pass
        score = round(float(similarity_score(target, comp)), 4)
        listings.append(
            {
                "listing_id": lid,
                "url": comp.url,
                "title": comp.title,
                "location": comp.location,
                "nightly_price": comp.nightly_price,
                "currency": comp.currency,
                "accommodates": comp.accommodates,
                "bedrooms": comp.bedrooms,
                "beds": comp.beds,
                "baths": comp.baths,
                "property_type": comp.property_type,
                "rating": comp.rating,
                "reviews": comp.reviews,
                "lat": comp.lat,
                "lng": comp.lng,
                "first_seen_offset": first_seen_offset.get(lid),
                "similarity_score": score,
            }
        )

    listings.sort(key=lambda x: x.get("similarity_score", 0.0), reverse=True)

    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "search": {
            "base_url": args.base_url,
            "query": args.query,
            "checkin": args.checkin,
            "checkout": checkout,
            "adults": args.adults,
            "items_per_grid": args.items_per_grid,
            "offsets": offsets,
            "hard_filters": {
                "guests": int(args.search_guests) if args.search_guests is not None else None,
                "minBedrooms": int(args.search_min_bedrooms) if args.search_min_bedrooms is not None else None,
                "minBeds": int(args.search_min_beds) if args.search_min_beds is not None else None,
                "minBathrooms": (
                    float(args.search_min_bathrooms) if args.search_min_bathrooms is not None else None
                ),
            },
            "per_offset_returned_ids": per_offset_counts,
        },
        "target": {
            "accommodates": target.accommodates,
            "bedrooms": target.bedrooms,
            "beds": target.beds,
            "baths": target.baths,
            "property_type": target.property_type,
            "amenities": target.amenities,
        },
        "summary": {
            "unique_listings": len(listings),
            "pdp_structural_strict": {
                "enabled": True,
                "attempted": pdp_structural_attempted_count,
                "updated": pdp_structural_updated_count,
            },
            "pdp_enrichment": {
                "enabled": bool(args.enrich_pdp_missing),
                "attempted": pdp_attempted_count,
                "updated": pdp_enriched_count,
            },
        },
        "listings": listings,
    }

    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_path = Path("worker") / "outputs" / f"search_similarity_{ts}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path.resolve()))


if __name__ == "__main__":
    main()
