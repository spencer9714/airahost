const COUNTRY_NAME_TO_CODE: Record<string, string> = {
  "united states": "US",
  "united states of america": "US",
  usa: "US",
  us: "US",
  taiwan: "TW",
  tw: "TW",
};

function cleanString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim();
  return text.length > 0 ? text : undefined;
}

function normalizeCountryCode(value: unknown): string | undefined {
  const text = cleanString(value);
  if (!text) return undefined;

  const upper = text.toUpperCase();
  if (/^[A-Z]{2}$/.test(upper)) {
    return upper;
  }

  return COUNTRY_NAME_TO_CODE[text.toLowerCase()];
}

function normalizePostalCode(value: unknown): string | undefined {
  const text = cleanString(value);
  if (!text) return undefined;
  return text.toUpperCase();
}

function buildPostalPrefix(postalCode: string | undefined): string | undefined {
  if (!postalCode) return undefined;
  const prefix = postalCode.replace(/[^A-Z0-9]/gi, "").slice(0, 3).toUpperCase();
  return prefix.length >= 3 ? prefix : undefined;
}

function parseLocationHint(inputAddress: string): Partial<Record<string, string>> {
  const text = cleanString(inputAddress);
  if (!text) return {};

  const airbnbParts = text.split("·").map((part) => part.trim()).filter(Boolean);
  const candidate = airbnbParts.length >= 3 ? airbnbParts[1] : text;
  const parts = candidate.split(",").map((part) => part.trim()).filter(Boolean);

  if (parts.length === 0) return {};

  const parsed: Partial<Record<string, string>> = {};
  const usStatePostal = parts.length >= 2
    ? parts[parts.length - 1].match(/^([A-Za-z]{2})\s+(\d{3,10}(?:-\d{4})?)$/)
    : null;

  if (parts.length >= 2) {
    parsed.city = parts[0];
  } else if (!candidate.startsWith("Airbnb Listing")) {
    parsed.city = candidate;
  }

  if (usStatePostal) {
    parsed.state = usStatePostal[1].toUpperCase();
    parsed.postalCode = usStatePostal[2].toUpperCase();
    parsed.countryCode = "US";
    parsed.country = "United States";
    return parsed;
  }

  const postalOnly = candidate.match(/\b([A-Za-z0-9][A-Za-z0-9 -]{2,10})\b$/);
  if (postalOnly && /\d/.test(postalOnly[1])) {
    parsed.postalCode = postalOnly[1].trim().toUpperCase();
  }

  return parsed;
}

export function enrichListingInputAttributes(
  inputAttributes: Record<string, unknown>,
  inputAddress: string
): Record<string, unknown> {
  const parsed = parseLocationHint(inputAddress);
  const city = cleanString(inputAttributes.city) ?? parsed.city;
  const state = cleanString(inputAttributes.state) ?? parsed.state;
  const postalCode = normalizePostalCode(
    inputAttributes.postalCode ?? inputAttributes.postal_code ?? parsed.postalCode
  );
  const countryCode = normalizeCountryCode(
    inputAttributes.countryCode ??
      inputAttributes.country_code ??
      parsed.countryCode
  );
  const country = cleanString(inputAttributes.country) ??
    cleanString(inputAttributes.addressCountry) ??
    parsed.country;

  const next: Record<string, unknown> = {
    ...inputAttributes,
    address: inputAddress,
  };

  if (city) next.city = city;
  if (state) next.state = state;
  if (postalCode) {
    next.postalCode = postalCode;
    next.postalCodePrefix = buildPostalPrefix(postalCode);
  }
  if (country) next.country = country;
  if (countryCode) next.countryCode = countryCode;

  if (
    city ||
    state ||
    postalCode ||
    country ||
    countryCode
  ) {
    next.locationSource =
      cleanString(inputAttributes.locationSource) ?? "user_input";
  }

  return next;
}
