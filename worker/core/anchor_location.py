"""
Location text utilities for anchor candidate selection (criteria pass 1).

Five-bucket hierarchy
---------------------
  local_match        — same city as target (best)
  nearby_market      — same metro cluster; geographically appropriate anchor
  regional_mismatch  — same state, different market area; usable only for
                       low/medium confidence targets
  far_mismatch       — different state; excluded except as fail-safe
  unknown            — location unparseable; treated as "location unknown",
                       never excluded (we cannot confirm it is far)

Metro cluster system
--------------------
Cities are grouped into named market clusters.  Two cities sharing a cluster
are classified as ``nearby_market``; same state but different clusters gives
``regional_mismatch``.

Cluster IDs use the format ``"<STATE>:<market>"``, e.g. ``"CA:bay_peninsula"``,
so identical city names in different states never collide.  Cities not listed
in any cluster fall back to ``regional_mismatch`` within their state (or
``far_mismatch`` across states) — conservative but safe.

Key design requirements (tested):
  Belmont vs Redwood City   → nearby_market    (same CA:bay_peninsula cluster)
  Belmont vs San Carlos     → nearby_market    (same CA:bay_peninsula cluster)
  Belmont vs San Mateo      → nearby_market    (same CA:bay_peninsula cluster)
  Belmont vs San Francisco  → regional_mismatch (CA:bay_sf ≠ CA:bay_peninsula)
  Belmont vs Sonoma         → regional_mismatch (CA:wine_country ≠ CA:bay_peninsula)
  Belmont vs Oakland        → regional_mismatch (CA:bay_east ≠ CA:bay_peninsula)
  Belmont vs Portland OR    → far_mismatch      (different state)
  San Jose vs Gilroy        → regional_mismatch (CA:bay_south_core ≠ CA:bay_south_far)

Fuzzy location normalisation
-----------------------------
``normalize_location_text`` applies conservative prefix/suffix stripping and
neighbourhood alias resolution before classification or geocoding.  Alias
tables are state-scoped where ambiguity exists (e.g. "Capitol Hill" in WA vs
DC).  Directional prefixes (North/South/East/West) are never stripped.

Path B (city-proxy geocoding)
-----------------------------
When page-embedded listing coords are unavailable, ``geocode_candidate_cities``
geocodes up to ``max_unique_cities`` unique location strings via Nominatim and
assigns city-centre proxy coordinates to qualifying candidates.  This is
Path B in ``_select_anchor_candidate``; geo-distance filtering at 15 km then
handles within-state distinctions that text buckets alone cannot make.
"""

from __future__ import annotations

import logging
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger("worker.core.anchor_location")

# ---------------------------------------------------------------------------
# State normalisation
# ---------------------------------------------------------------------------

_US_STATE_FULL_TO_CODE: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def normalize_state(state_str: str) -> str:
    """
    Return a canonical 2-letter uppercase code for US states.
    For non-US regions returns the lowercased input as-is.
    Returns "" for empty/whitespace-only input.
    """
    if not state_str or not state_str.strip():
        return ""
    s = state_str.strip().lower()
    if len(s) == 2 and s.isalpha():
        return s.upper()
    code = _US_STATE_FULL_TO_CODE.get(s)
    if code:
        return code
    return s  # non-US state/province — preserve as normalised lower


def normalize_city(city: str) -> str:
    """Lowercase + strip a city name for comparison."""
    return city.strip().lower() if city else ""


# ---------------------------------------------------------------------------
# Location text parsing
# ---------------------------------------------------------------------------


def parse_location_city_state(location_text: str) -> Tuple[str, str]:
    """
    Parse an Airbnb search-result location string into (city, state).

    Typical formats:
      "Belmont, California"    → ("belmont", "CA")
      "San Mateo, CA"          → ("san mateo", "CA")
      "Redwood City, California" → ("redwood city", "CA")
      "Paris, Île-de-France"   → ("paris", "île-de-france")
      "London"                 → ("london", "")

    Returns ("", "") when location_text is empty or unparseable.
    """
    if not location_text or not location_text.strip():
        return "", ""
    parts = [p.strip() for p in location_text.split(",")]
    if len(parts) >= 2:
        return normalize_city(parts[0]), normalize_state(parts[1])
    return normalize_city(parts[0]), ""


# ---------------------------------------------------------------------------
# Metro market clusters
# ---------------------------------------------------------------------------
# Cluster naming: "<2-LETTER-STATE>:<market_name>"
# A city may appear in at most one cluster per state.
# Cities outside any cluster fall back to regional_mismatch within their state.

_METRO_CLUSTERS: Dict[str, FrozenSet[str]] = {

    # ── Bay Area: SF Peninsula ────────────────────────────────────────────────
    # South of SF along US-101/I-280; tight geographic corridor (~50 km long)
    "CA:bay_peninsula": frozenset({
        "belmont", "san carlos", "redwood city", "san mateo", "burlingame",
        "hillsborough", "millbrae", "foster city", "san bruno",
        "south san francisco", "brisbane", "daly city", "colma", "pacifica",
        "menlo park", "palo alto", "east palo alto", "atherton",
        "portola valley", "woodside", "half moon bay",
    }),

    # ── Bay Area: San Francisco (standalone high-density market) ─────────────
    "CA:bay_sf": frozenset({
        "san francisco",
    }),

    # ── Bay Area: Inner East Bay ──────────────────────────────────────────────
    "CA:bay_east": frozenset({
        "oakland", "berkeley", "emeryville", "alameda", "el cerrito",
        "richmond", "san leandro", "hayward", "fremont", "union city",
        "newark", "san lorenzo", "castro valley",
    }),

    # ── Bay Area: Tri-Valley / Inland East Bay ────────────────────────────────
    "CA:bay_trivalley": frozenset({
        "pleasanton", "livermore", "dublin", "san ramon", "danville",
        "walnut creek", "concord", "martinez", "lafayette", "orinda",
        "moraga", "antioch", "pittsburg",
        # Note: "brentwood" omitted — collision with Brentwood neighborhood in LA
    }),

    # ── Bay Area: South Bay / Silicon Valley Core ─────────────────────────────
    # San Jose metro; ~10–15 km radius from downtown.
    "CA:bay_south_core": frozenset({
        "san jose", "campbell", "los gatos", "saratoga", "los altos",
        "los altos hills", "milpitas", "santa clara", "cupertino",
        "sunnyvale", "mountain view",
    }),

    # ── Bay Area: Far South Bay ───────────────────────────────────────────────
    # Gilroy is ~45 km from San Jose; treating it as a distinct sub-market
    # prevents San Jose vs Gilroy from being classified as "nearby_market".
    "CA:bay_south_far": frozenset({
        "gilroy", "morgan hill",
    }),

    # ── Bay Area: North Bay (Marin County) ───────────────────────────────────
    "CA:bay_north": frozenset({
        "sausalito", "tiburon", "mill valley", "san rafael", "novato",
        "corte madera", "larkspur", "fairfax", "ross", "kentfield",
        "marin city",
    }),

    # ── Wine Country (Sonoma / Napa) ─────────────────────────────────────────
    "CA:wine_country": frozenset({
        "sonoma", "napa", "santa rosa", "petaluma", "healdsburg",
        "cloverdale", "sebastopol", "rohnert park", "windsor",
        "calistoga", "st. helena", "yountville",
    }),

    # ── Greater LA: West Side & Beach Cities ─────────────────────────────────
    "CA:la_westside": frozenset({
        "santa monica", "venice", "marina del rey", "playa del rey",
        "el segundo", "manhattan beach", "hermosa beach", "redondo beach",
        "torrance", "culver city", "west hollywood", "pacific palisades",
        "malibu",
    }),

    # ── Greater LA: Central / Hollywood ──────────────────────────────────────
    "CA:la_central": frozenset({
        "los angeles", "hollywood", "silver lake", "echo park", "koreatown",
        "beverly hills", "west los angeles", "brentwood",
    }),

    # ── Greater LA: San Fernando Valley ──────────────────────────────────────
    "CA:la_valley": frozenset({
        "burbank", "glendale", "studio city", "sherman oaks", "van nuys",
        "north hollywood", "encino", "woodland hills", "chatsworth",
        "calabasas", "reseda",
    }),

    # ── Greater LA: San Gabriel Valley ───────────────────────────────────────
    "CA:la_sgv": frozenset({
        "pasadena", "alhambra", "arcadia", "monrovia", "temple city",
        "san gabriel", "rosemead", "el monte",
    }),

    # ── Greater LA: Orange County ─────────────────────────────────────────────
    "CA:la_oc": frozenset({
        "anaheim", "santa ana", "irvine", "huntington beach", "costa mesa",
        "newport beach", "laguna beach", "san clemente", "mission viejo",
        "lake forest", "garden grove", "fullerton",
    }),

    # ── San Diego Metro ───────────────────────────────────────────────────────
    "CA:san_diego": frozenset({
        "san diego", "la jolla", "pacific beach", "mission beach",
        "ocean beach", "coronado", "chula vista", "national city",
        "encinitas", "carlsbad", "oceanside", "del mar", "solana beach",
        "escondido",
    }),

    # ── New York Metro ────────────────────────────────────────────────────────
    "NY:manhattan": frozenset({
        "new york", "new york city", "manhattan",
    }),
    "NY:brooklyn": frozenset({
        "brooklyn",
    }),
    "NY:queens": frozenset({
        "queens", "astoria", "long island city", "flushing",
        "jamaica", "jackson heights",
    }),
    "NY:bronx": frozenset({
        "bronx",
    }),

    # ── Seattle / Eastside ────────────────────────────────────────────────────
    "WA:seattle": frozenset({
        "seattle", "bellevue", "redmond", "kirkland", "renton",
        "mercer island", "shoreline", "edmonds", "bothell", "kent",
        "burien", "tukwila",
    }),

    # ── Texas ─────────────────────────────────────────────────────────────────
    "TX:austin": frozenset({
        "austin", "round rock", "cedar park", "leander", "pflugerville",
        "georgetown", "manor", "buda", "kyle", "lakeway", "bee cave",
        "rollingwood",
    }),
    "TX:dallas": frozenset({
        "dallas", "plano", "frisco", "mckinney", "allen", "garland",
        "mesquite", "irving", "grand prairie", "richardson", "addison",
    }),
    "TX:houston": frozenset({
        "houston", "sugar land", "pearland", "pasadena", "baytown",
        "katy", "the woodlands", "spring", "humble",
    }),

    # ── Florida ───────────────────────────────────────────────────────────────
    "FL:miami": frozenset({
        "miami", "miami beach", "coral gables", "key biscayne",
        "north miami", "north miami beach", "aventura", "bal harbour",
        "surfside", "sunny isles beach",
    }),
    "FL:fort_lauderdale": frozenset({
        "fort lauderdale", "hallandale beach", "hollywood",
        "deerfield beach", "pompano beach", "lighthouse point", "dania beach",
    }),
    "FL:orlando": frozenset({
        "orlando", "kissimmee", "celebration", "winter garden",
        "altamonte springs", "lake buena vista", "clermont",
    }),
    "FL:tampa": frozenset({
        "tampa", "st. petersburg", "clearwater", "bradenton",
        "sarasota", "dunedin", "palm harbor",
    }),

    # ── Illinois ──────────────────────────────────────────────────────────────
    "IL:chicago": frozenset({
        "chicago", "evanston", "skokie", "oak park", "cicero",
        "berwyn",
    }),

    # ── Colorado ──────────────────────────────────────────────────────────────
    "CO:denver": frozenset({
        "denver", "aurora", "lakewood", "arvada", "westminster",
        "thornton", "centennial", "littleton", "englewood", "golden",
    }),

    # ── Tennessee ─────────────────────────────────────────────────────────────
    "TN:nashville": frozenset({
        "nashville", "franklin", "murfreesboro",
        "smyrna", "la vergne", "nolensville", "spring hill",
    }),

    # ── Georgia ───────────────────────────────────────────────────────────────
    "GA:atlanta": frozenset({
        "atlanta", "brookhaven", "decatur", "marietta", "roswell",
        "alpharetta", "johns creek", "sandy springs", "dunwoody",
        "duluth", "lawrenceville", "tucker",
    }),

    # ── North Carolina ────────────────────────────────────────────────────────
    "NC:charlotte": frozenset({
        "charlotte", "concord", "gastonia", "rock hill", "fort mill",
        "huntersville", "cornelius", "matthews", "monroe",
    }),
    "NC:triangle": frozenset({
        "raleigh", "durham", "chapel hill", "cary", "apex",
        "wake forest", "morrisville", "holly springs",
    }),

    # ── Massachusetts ─────────────────────────────────────────────────────────
    "MA:boston": frozenset({
        "boston", "cambridge", "somerville", "brookline", "newton",
        "watertown", "waltham", "quincy", "medford", "malden",
        "everett", "chelsea",
    }),

    # ── Arizona ───────────────────────────────────────────────────────────────
    "AZ:phoenix": frozenset({
        "phoenix", "scottsdale", "tempe", "mesa", "chandler",
        "gilbert", "peoria", "glendale", "surprise", "goodyear",
    }),

    # ── Nevada ────────────────────────────────────────────────────────────────
    "NV:las_vegas": frozenset({
        "las vegas", "henderson", "north las vegas", "paradise",
        "enterprise", "spring valley", "summerlin", "whitney",
    }),

    # ── Oregon ────────────────────────────────────────────────────────────────
    # Portland metro (Willamette Valley).  Vancouver, WA is excluded — it is
    # in a different state and handled by its own cluster if needed.
    "OR:portland": frozenset({
        "portland", "beaverton", "hillsboro", "gresham", "lake oswego",
        "tigard", "tualatin", "oregon city", "milwaukie", "west linn",
        "happy valley", "clackamas", "fairview", "troutdale",
    }),

    # ── Minnesota ─────────────────────────────────────────────────────────────
    # Twin Cities metro.  Both "saint paul" and "st. paul" are included
    # because Airbnb listings use both spellings.
    "MN:minneapolis": frozenset({
        "minneapolis", "saint paul", "st. paul", "bloomington", "eden prairie",
        "minnetonka", "plymouth", "maple grove", "brooklyn park",
        "eagan", "burnsville", "edina", "apple valley",
        "richfield", "st. louis park", "woodbury", "roseville", "coon rapids",
    }),

    # ── Michigan ──────────────────────────────────────────────────────────────
    # Detroit metro; Ann Arbor (~63 km) is excluded — too far to be a
    # meaningful nearby-market anchor for a Detroit listing.
    "MI:detroit": frozenset({
        "detroit", "dearborn", "dearborn heights", "hamtramck",
        "lincoln park", "allen park", "taylor",
        "ferndale", "hazel park", "oak park", "royal oak",
        "warren", "eastpointe", "roseville", "st. clair shores",
        "livonia", "westland", "garden city", "redford",
        "southfield", "farmington hills", "troy", "sterling heights",
    }),
}

# Reverse lookup: (state_code_upper, city_lower) → cluster_id
_CITY_TO_CLUSTER: Dict[Tuple[str, str], str] = {}
for _cluster_id, _cities in _METRO_CLUSTERS.items():
    _state_code = _cluster_id.split(":")[0]  # "CA" from "CA:bay_peninsula"
    for _city in _cities:
        _CITY_TO_CLUSTER[(_state_code, _city)] = _cluster_id
del _cluster_id, _cities, _state_code, _city  # cleanup module namespace


def get_city_cluster(state_code: str, city: str) -> Optional[str]:
    """
    Return the market cluster ID for a (state, city) pair, or None if not
    in any cluster.

    ``state_code`` should be a 2-letter code; ``city`` is lowercased before
    lookup.  Case of ``state_code`` is normalised internally.
    """
    return _CITY_TO_CLUSTER.get((state_code.upper(), city.strip().lower()))


def get_nearby_cities(state_code: str, city: str) -> List[str]:
    """
    Return the approved nearby cities for a target city — i.e., every other
    city in the same metro cluster, sorted alphabetically.

    These are the cities that ``_select_anchor_candidate`` considers an
    acceptable "nearby-market expansion" when no target-city candidates are
    found.

    Returns an empty list when the city is not in any cluster (unknown market),
    signalling that nearby-market expansion is not available.

    Args:
        state_code: 2-letter state code (case-insensitive).
        city:       City name (any case; normalised internally).

    Examples:
        >>> get_nearby_cities("CA", "Belmont")
        ['atherton', 'brisbane', 'burlingame', 'colma', ...]  # all CA:bay_peninsula
        >>> get_nearby_cities("CA", "Sonoma")
        ['calistoga', 'cloverdale', ...]  # CA:wine_country, not Peninsula
        >>> get_nearby_cities("CA", "unknown_city")
        []
    """
    cluster = get_city_cluster(state_code, city)
    if not cluster:
        return []
    norm_city = city.strip().lower()
    return sorted(
        c for c in _METRO_CLUSTERS.get(cluster, frozenset()) if c != norm_city
    )


# ---------------------------------------------------------------------------
# Location text normalisation (fuzzy / neighbourhood-aware)
# ---------------------------------------------------------------------------
# These tables are deliberately conservative — a missed normalisation (false
# negative) is far less harmful than mapping a listing to the wrong city
# (false positive).

# Qualifier prefixes that are safe to strip from the *start* of a city token.
# Directional prefixes (North/South/East/West) are intentionally absent:
# "North Hollywood", "West Hollywood", "South San Francisco", and
# "East Palo Alto" are distinct incorporated municipalities with their own
# cluster IDs and must not be collapsed onto their base city.
_LOCATION_PREFIX_QUALIFIERS: Tuple[str, ...] = (
    "old town",   # must precede "old" (longest-match first)
    "downtown",
    "uptown",
    "midtown",
    "greater",
    "near",
    "old",
)

# Globally-distinctive neighbourhood aliases (no state context required).
# Only names that are virtually unambiguous worldwide belong here.
_NEIGHBORHOOD_ALIASES_UNSCOPED: Dict[str, str] = {
    "soma":            "san francisco",
    "south of market": "san francisco",
}

# State-scoped neighbourhood aliases: (alias_lower, STATE_CODE) → canonical_city.
# Requiring the state part prevents false positives from same-name
# neighbourhoods in other cities (e.g. "Capitol Hill" exists in WA and DC).
_NEIGHBORHOOD_ALIASES_SCOPED: Dict[Tuple[str, str], str] = {
    # San Francisco neighbourhoods
    ("noe valley",         "CA"): "san francisco",
    ("the castro",         "CA"): "san francisco",
    ("mission district",   "CA"): "san francisco",
    ("haight-ashbury",     "CA"): "san francisco",
    ("north beach",        "CA"): "san francisco",
    ("russian hill",       "CA"): "san francisco",
    ("pacific heights",    "CA"): "san francisco",
    ("financial district", "CA"): "san francisco",
    ("potrero hill",       "CA"): "san francisco",
    ("dogpatch",           "CA"): "san francisco",
    ("the mission",        "CA"): "san francisco",
    ("hayes valley",       "CA"): "san francisco",
    ("inner sunset",       "CA"): "san francisco",
    ("outer sunset",       "CA"): "san francisco",
    # LA neighbourhoods (not standalone cluster entries)
    ("hollywood hills",    "CA"): "los angeles",
    # Belmont-area neighbourhoods
    ("belmont hills",      "CA"): "belmont",
    # NYC neighbourhoods
    ("williamsburg",       "NY"): "brooklyn",
    ("bushwick",           "NY"): "brooklyn",
    ("park slope",         "NY"): "brooklyn",
    ("bed-stuy",           "NY"): "brooklyn",
    ("bedford-stuyvesant", "NY"): "brooklyn",
    ("harlem",             "NY"): "new york",
    ("upper east side",    "NY"): "new york",
    ("upper west side",    "NY"): "new york",
    ("lower east side",    "NY"): "new york",
    ("tribeca",            "NY"): "new york",
    ("soho",               "NY"): "new york",
    ("chelsea",            "NY"): "new york",
    ("hells kitchen",      "NY"): "new york",
    ("hell's kitchen",     "NY"): "new york",
    # Chicago neighbourhoods
    ("wicker park",        "IL"): "chicago",
    ("lincoln park",       "IL"): "chicago",
    ("wrigleyville",       "IL"): "chicago",
    ("logan square",       "IL"): "chicago",
    ("pilsen",             "IL"): "chicago",
    # Seattle neighbourhoods — must be state-scoped because "fremont"
    # is also a city in CA (CA:bay_east cluster).
    ("fremont",            "WA"): "seattle",
    ("ballard",            "WA"): "seattle",
    ("capitol hill",       "WA"): "seattle",
    ("south lake union",   "WA"): "seattle",
    ("queen anne",         "WA"): "seattle",
}


def normalize_location_text(raw: str) -> Tuple[str, str]:
    """
    Conservatively normalise an Airbnb location label for geocoding and
    text-bucket classification.

    Returns ``(normalized_text, notes)`` where *notes* is a human-readable
    string describing the transformation applied (empty string if none).

    Rules applied in order — alias rules fire on first match; stripping rules
    3–5 can chain (e.g. ``"Greater Boston Area"`` → strip prefix → strip suffix
    → ``"Boston"``):

    1. State-scoped neighbourhood alias
       ``"Noe Valley, CA"``       → ``"san francisco, CA"``
    2. Unscoped globally-distinctive alias
       ``"SoMa"``                 → ``"san francisco"``
    3. Strip qualifier prefix
       ``"Downtown San Francisco, CA"`` → ``"San Francisco, CA"``
    4. Strip ``" County"`` suffix
       ``"San Mateo County, CA"`` → ``"San Mateo, CA"``
    5. Strip ``" Area"`` suffix
       ``"Greater Boston Area, MA"`` → ``"Boston, MA"`` (chains with rule 3)

    Directional prefixes (North/South/East/West) are *not* stripped because
    ``"North Hollywood"``, ``"West Hollywood"``, and ``"South San Francisco"``
    are distinct incorporated municipalities.

    If no rule fires, the original string is returned unchanged with notes="".
    """
    if not raw:
        return "", ""
    stripped = raw.strip()
    if not stripped:
        return "", ""

    # Split on the first comma only; preserve full state/country suffix.
    comma_idx = stripped.find(",")
    if comma_idx >= 0:
        raw_city_part = stripped[:comma_idx].strip()
        state_suffix  = stripped[comma_idx:]        # e.g. ", California"
        raw_state     = stripped[comma_idx + 1:].strip()
        state_code    = normalize_state(raw_state)
    else:
        raw_city_part = stripped
        state_suffix  = ""
        state_code    = ""

    city_lower = raw_city_part.lower()

    # ── Rule 1: state-scoped alias ───────────────────────────────────────────
    if state_code and (city_lower, state_code) in _NEIGHBORHOOD_ALIASES_SCOPED:
        canonical = _NEIGHBORHOOD_ALIASES_SCOPED[(city_lower, state_code)]
        return f"{canonical}{state_suffix}", f"alias:{city_lower}"

    # ── Rule 2: unscoped alias ───────────────────────────────────────────────
    if city_lower in _NEIGHBORHOOD_ALIASES_UNSCOPED:
        canonical = _NEIGHBORHOOD_ALIASES_UNSCOPED[city_lower]
        return f"{canonical}{state_suffix}", f"alias:{city_lower}"

    # ── Rules 3–5: stripping (can chain on the same city token) ─────────────
    cleaned       = raw_city_part    # preserve original case for geocoding
    cleaned_lower = city_lower
    applied: List[str] = []

    # Rule 3: qualifier prefix
    for prefix in _LOCATION_PREFIX_QUALIFIERS:
        if (
            cleaned_lower.startswith(prefix + " ")
            and len(cleaned_lower) > len(prefix) + 1
        ):
            remainder = cleaned[len(prefix) + 1:].strip()
            if remainder:
                cleaned       = remainder
                cleaned_lower = remainder.lower()
                applied.append(f"prefix:{prefix}")
            break  # strip at most one prefix

    # Rule 4: " County" suffix
    if cleaned_lower.endswith(" county"):
        remainder = cleaned[: -len(" county")].strip()
        if remainder:
            cleaned       = remainder
            cleaned_lower = remainder.lower()
            applied.append("suffix:county")

    # Rule 5: " Area" suffix
    if cleaned_lower.endswith(" area"):
        remainder = cleaned[: -len(" area")].strip()
        if remainder:
            cleaned = remainder
            applied.append("suffix:area")

    if applied:
        return f"{cleaned}{state_suffix}", ",".join(applied)
    return stripped, ""


# ---------------------------------------------------------------------------
# Text-bucket classification (Path C)
# ---------------------------------------------------------------------------


def classify_candidate_location(
    candidate_location: str,
    target_city: str,
    target_state: str,
) -> str:
    """
    Classify a candidate's location string relative to the target.

    Returns one of five buckets:
      "local_match"        — same city
      "nearby_market"      — same metro cluster (geographically appropriate)
      "regional_mismatch"  — same state, different market area
      "far_mismatch"       — different state/country
      "unknown"            — cannot parse location; treated as pass-through

    ``target_city`` / ``target_state`` need not be pre-normalised; this
    function normalises them internally.  If either is empty, returns "unknown"
    because a meaningful comparison cannot be made.
    """
    if not candidate_location or not target_city or not target_state:
        return "unknown"

    # Normalise neighbourhood labels and qualifier prefixes before parsing.
    norm_loc, _ = normalize_location_text(candidate_location)
    cand_city, cand_state = parse_location_city_state(norm_loc)
    norm_target_city = normalize_city(target_city)
    norm_target_state = normalize_state(target_state)

    if not cand_state or not norm_target_state:
        # Can't determine cross-state status — unknown
        return "unknown"

    # Cross-state → far mismatch
    if cand_state != norm_target_state:
        return "far_mismatch"

    # Same city → local match
    if cand_city == norm_target_city:
        return "local_match"

    # Same state, different city — check metro cluster
    target_cluster = get_city_cluster(norm_target_state, norm_target_city)
    cand_cluster = get_city_cluster(cand_state, cand_city)

    if target_cluster and cand_cluster and target_cluster == cand_cluster:
        return "nearby_market"

    # Same state but different (or no) cluster → regional mismatch
    # This deliberately separates Peninsula/SF/East Bay/Wine Country etc.
    return "regional_mismatch"


# ---------------------------------------------------------------------------
# City-proxy geocoding (Path B)
# ---------------------------------------------------------------------------


def geocode_candidate_cities(
    candidates,  # List[ListingSpec] — avoids circular import
    max_unique_cities: int = 10,
    timeout_per_city: int = 2,
) -> int:
    """
    Geocode unique candidate location strings and assign city-centre proxy
    coordinates to candidates that currently have no listing-level coords.

    Only candidates with a non-empty ``.location`` attribute and ``lat==None``
    are processed.  Candidates that already have listing-level coords are
    skipped.

    The location text is lightly normalised before geocoding (stripped of
    leading/trailing whitespace) to maximise Nominatim hit rate.  Results are
    cached within the call so each unique location is geocoded at most once.

    At most ``max_unique_cities`` unique location strings are geocoded to cap
    external Nominatim API calls.  The default is 10 — higher than the
    previous 5 to improve coverage in markets with many distinct suburbs.

    Returns:
        Number of candidates that received proxy coordinates.

    Side-effect:
        Sets ``.lat`` and ``.lng`` on qualifying candidates.
    """
    try:
        from worker.core.geocode_details import geocode_address_details
    except ImportError:
        logger.warning(
            "[anchor_location] geocode_details not available; "
            "skipping city-proxy geocoding"
        )
        return 0

    # Collect unique location texts from candidates that lack coords
    location_cache: Dict[str, Optional[Tuple[float, float]]] = {}
    for cand in candidates:
        if (
            getattr(cand, "lat", None) is not None
            and getattr(cand, "lng", None) is not None
        ):
            continue  # already has listing-level coords — skip

        raw_loc = (getattr(cand, "location", "") or "").strip()
        if not raw_loc:
            continue
        # Normalise before geocoding: "Downtown San Francisco, CA" → "San Francisco, CA"
        norm_loc, _ = normalize_location_text(raw_loc)
        loc = norm_loc if norm_loc else raw_loc
        if loc not in location_cache:
            if len(location_cache) < max_unique_cities:
                location_cache[loc] = None  # queued, not yet geocoded

    if not location_cache:
        logger.debug("[anchor_location] No candidates need city-proxy geocoding")
        return 0

    logger.info(
        f"[anchor_location] City-proxy geocoding {len(location_cache)} unique "
        f"location(s) (cap={max_unique_cities})"
    )

    # Geocode each unique location string
    for loc in list(location_cache.keys()):
        result = geocode_address_details(loc, timeout=timeout_per_city)
        if result and result.get("lat") is not None and result.get("lng") is not None:
            location_cache[loc] = (float(result["lat"]), float(result["lng"]))
            logger.debug(
                f"[anchor_location] Geocoded {loc!r} → "
                f"({result['lat']:.4f}, {result['lng']:.4f})"
            )
        else:
            logger.debug(f"[anchor_location] Geocode failed for {loc!r}")

    # Assign proxy coords to candidates (use same normalised key as collection)
    assigned = 0
    for cand in candidates:
        if getattr(cand, "lat", None) is not None:
            continue  # listing-level coords take priority
        raw_loc = (getattr(cand, "location", "") or "").strip()
        if not raw_loc:
            continue
        norm_loc, _ = normalize_location_text(raw_loc)
        loc = norm_loc if norm_loc else raw_loc
        coords = location_cache.get(loc)
        if coords:
            cand.lat, cand.lng = coords[0], coords[1]
            assigned += 1

    logger.info(
        f"[anchor_location] City-proxy geocoding: {assigned} candidate(s) "
        f"assigned proxy coords"
    )
    return assigned
