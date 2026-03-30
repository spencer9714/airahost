/**
 * Extracts the numeric Airbnb listing ID from a given URL.
 *
 * Supports URLs like:
 * - https://www.airbnb.com/rooms/12345
 * - https://www.airbnb.com/rooms/plus/12345
 * - /rooms/12345
 * - and URLs with query parameters.
 *
 * @param url The Airbnb listing URL.
 * @returns The numeric listing ID as a string, or null if not found.
 */
export function extractAirbnbListingId(
  url: string | null | undefined
): string | null {
  if (!url) {
    return null;
  }

  const match = url.match(/(?:\/rooms\/|\/listings\/)(?:plus\/)?(\d+)/);
  return match && match[1] ? match[1] : null;
}