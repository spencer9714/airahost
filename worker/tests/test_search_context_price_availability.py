from worker.scraper.parsers import (
    _extract_availability_context_from_search_result,
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
