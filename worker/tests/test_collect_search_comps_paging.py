import base64
from datetime import date

from worker.scraper.comp_collection import collect_search_comps


def _gid(listing_id: str) -> str:
    raw = f"DemandStayListing:{listing_id}".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def _payload(listing_id: str, price_text: str):
    return {
        "data": {
            "presentation": {
                "staysSearch": {
                    "results": {
                        "searchResults": [
                            {
                                "demandStayListing": {"id": _gid(listing_id)},
                                "available": True,
                                "structuredDisplayPrice": {
                                    "primaryLine": {
                                        "price": price_text,
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


class _FakeClient:
    def __init__(self):
        self.calls = []

    def search_listings_with_overrides(self, overrides):
        self.calls.append(dict(overrides))
        offset = int(overrides.get("itemsOffset") or 0)
        if offset == 0:
            return 200, _payload("111", "$200 CAD")
        if offset == 20:
            return 200, _payload("222", "$300 CAD")
        return 200, {"data": {"presentation": {"staysSearch": {"results": {"searchResults": []}}}}}


def test_collect_search_comps_merges_offsets():
    client = _FakeClient()
    comps, qn = collect_search_comps(
        client=client,
        search_location="Toronto, ON",
        base_origin="https://www.airbnb.ca",
        date_i=date(2026, 5, 6),
        adults=2,
        max_scroll_rounds=1,
        max_cards=20,
        rate_limit_seconds=0.0,
        page_offsets=[0, 20],
    )

    urls = sorted([c.url for c in comps])
    assert qn == 1
    assert "https://www.airbnb.ca/rooms/111" in urls
    assert "https://www.airbnb.ca/rooms/222" in urls
