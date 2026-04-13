from worker.scraper.price_estimator import _build_daily_transparent_result
from worker.scraper.target_extractor import ListingSpec


def test_price_by_date_backfills_early_day_from_full_comp_prices():
    target = ListingSpec(
        url="https://www.airbnb.com/rooms/999",
        title="Target listing",
        location="Belmont, CA",
        property_type="entire_home",
        accommodates=4,
        bedrooms=2,
        baths=1.5,
        nightly_price=200,
    )

    all_day_results = [
        {
            "date": "2026-05-01",
            "median_price": 180,
            "comps_collected": 2,
            "comps_used": 1,
            "below_similarity_floor": 0,
            "price_outliers_excluded": 0,
            "price_outliers_downweighted": 0,
            "geo_excluded": 0,
            "price_band_excluded": 0,
            "filter_stage": "strict",
            "flags": [],
            "is_sampled": True,
            "is_weekend": False,
            "price_distribution": {},
            "top_comps": [
                {
                    "id": "111",
                    "title": "Comp A",
                    "propertyType": "entire_home",
                    "nightlyPrice": 120,
                    "similarity": 0.91,
                    "url": "https://www.airbnb.com/rooms/111",
                }
            ],
            "comp_prices": {
                "111": 120,
                "222": 140,
            },
            "error": None,
            "selection_mode": "strict",
            "pricing_confidence": "high",
        },
        {
            "date": "2026-05-02",
            "median_price": 185,
            "comps_collected": 2,
            "comps_used": 1,
            "below_similarity_floor": 0,
            "price_outliers_excluded": 0,
            "price_outliers_downweighted": 0,
            "geo_excluded": 0,
            "price_band_excluded": 0,
            "filter_stage": "strict",
            "flags": [],
            "is_sampled": True,
            "is_weekend": True,
            "price_distribution": {},
            "top_comps": [
                {
                    "id": "222",
                    "title": "Comp B",
                    "propertyType": "entire_home",
                    "nightlyPrice": 150,
                    "similarity": 0.9,
                    "url": "https://www.airbnb.com/rooms/222",
                }
            ],
            "comp_prices": {
                "222": 150,
            },
            "error": None,
            "selection_mode": "strict",
            "pricing_confidence": "high",
        },
    ]

    transparent = _build_daily_transparent_result(
        target=target,
        query_criteria={
            "locationBasis": "Belmont, CA",
            "searchAdults": 4,
            "checkin": "2026-05-01",
            "checkout": "2026-05-03",
            "totalNights": 2,
            "sampledNights": 2,
            "queryMode": "day_by_day",
            "propertyTypeFilter": "entire_home",
        },
        all_day_results=all_day_results,
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
    )

    comps = transparent["comparableListings"]
    comp_b = next(c for c in comps if c["id"] == "222")
    assert comp_b["priceByDate"]["2026-05-01"] == 140
    assert comp_b["priceByDate"]["2026-05-02"] == 150
