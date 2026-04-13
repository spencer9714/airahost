from worker.scraper.parsers import (
    _extract_availability_context_from_search_result,
    parse_pdp_response,
    parse_search_listing_context,
    _parse_price_with_prefix,
    _parse_price_string,
)


def test_parse_price_string_prefers_currency_amount_over_other_numbers():
    text = "Rated 4.91 out of 5. CA$241 for 1 night"
    assert _parse_price_string(text) == 241.0


def test_parse_price_with_prefix_returns_raw_prefix_token():
    text = "XCUR241 for 2 nights"
    value, prefix = _parse_price_with_prefix(text)
    assert value == 241.0
    assert prefix == "XCUR"


def test_availability_does_not_flip_unavailable_from_generic_word_only():
    payload = {
        "title": "Nice condo",
        "someNestedText": "Unavailable amenity: hot tub",
    }
    out = _extract_availability_context_from_search_result(payload)
    assert out["is_available"] is True
    assert out["availability_reason"] is None


def test_availability_detects_strong_unavailable_markers():
    payload = {
        "banner": "Sold out for your dates",
    }
    out = _extract_availability_context_from_search_result(payload)
    assert out["is_available"] is False
    assert out["availability_reason"] == "sold_out"


def test_parse_search_context_uses_structured_primary_price_when_available():
    payload = {
        "data": {
            "presentation": {
                "staysSearch": {
                    "results": {
                        "searchResults": [
                            {
                                "demandStayListing": {
                                    # base64("DemandStayListing:1629301846268091180")
                                    "id": "RGVtYW5kU3RheUxpc3Rpbmc6MTYyOTMwMTg0NjI2ODA5MTE4MA=="
                                },
                                "available": True,
                                "structuredDisplayPrice": {
                                    "primaryLine": {
                                        "accessibilityLabel": "$173 CAD total",
                                        "price": "$173 CAD",
                                        "qualifier": "total",
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
    }

    ctx = parse_search_listing_context(payload)
    row = ctx["1629301846268091180"]
    assert row["is_available"] is True
    assert row["total_price"] == 173.0
    assert row["currency"] == "CAD"


def test_parse_search_context_does_not_apply_structured_primary_price_when_unavailable():
    payload = {
        "data": {
            "presentation": {
                "staysSearch": {
                    "results": {
                        "searchResults": [
                            {
                                "demandStayListing": {
                                    # base64("DemandStayListing:1629301846268091180")
                                    "id": "RGVtYW5kU3RheUxpc3Rpbmc6MTYyOTMwMTg0NjI2ODA5MTE4MA=="
                                },
                                "available": False,
                                "structuredDisplayPrice": {
                                    "primaryLine": {
                                        "accessibilityLabel": "$173 CAD total",
                                        "price": "$173 CAD",
                                        "qualifier": "total",
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
    }

    ctx = parse_search_listing_context(payload)
    row = ctx["1629301846268091180"]
    assert row["is_available"] is False
    assert row["total_price"] is None
    assert row["currency"] is None


def test_parse_pdp_response_uses_book_it_floating_footer_structure_only():
    payload = {
        "data": {
            "presentation": {
                "stayProductDetailPage": {
                    "sections": {
                        "sections": [
                            {
                                "sectionId": "BOOK_IT_FLOATING_FOOTER",
                                "section": {
                                    "available": True,
                                    "structuredDisplayPrice": {
                                        "primaryLine": {
                                            "accessibilityLabel": "$173 CAD total",
                                            "price": "$173 CAD",
                                            "qualifier": "total",
                                        }
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
    }

    out = parse_pdp_response(payload, "1629301846268091180", "https://www.airbnb.ca")
    assert out["total_price"] == 173.0
    assert out["nightly_price"] == 173.0
    assert out["currency"] == "CAD"


def test_parse_pdp_response_uses_book_it_sidebar_when_footer_absent():
    payload = {
        "data": {
            "presentation": {
                "stayProductDetailPage": {
                    "sections": {
                        "sections": [
                            {
                                "sectionId": "POLICIES_DEFAULT",
                                "section": {"available": True},
                            },
                            {
                                "sectionId": "BOOK_IT_SIDEBAR",
                                "section": {
                                    "available": True,
                                    "structuredDisplayPrice": {
                                        "primaryLine": {
                                            "accessibilityLabel": "$480 CAD total",
                                            "price": "$480 CAD",
                                            "qualifier": "total",
                                        }
                                    },
                                },
                            },
                        ]
                    }
                }
            }
        }
    }

    out = parse_pdp_response(payload, "1629301846268091180", "https://www.airbnb.ca")
    assert out["total_price"] == 480.0
    assert out["nightly_price"] == 480.0
    assert out["currency"] == "CAD"
