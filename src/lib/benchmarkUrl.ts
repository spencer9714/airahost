/**
 * Shared Airbnb benchmark URL validation helpers.
 *
 * Single source of truth used by:
 *   - /api/validate-benchmark/route.ts  (server — blur validation)
 *   - src/app/tool/page.tsx             (client — submit idle fallback)
 *
 * Keeping the regex here prevents the two validation paths from drifting apart.
 */

/**
 * Matches Airbnb listing URLs across supported domains:
 *   airbnb.com/rooms/12345
 *   airbnb.com.tw/rooms/12345   (com + 2-letter country suffix)
 *   airbnb.de/rooms/12345       (2-letter TLD)
 *   www.airbnb.com/rooms/12345  (with optional www)
 *
 * Capture group 4 is the numeric room ID.
 */
export const AIRBNB_ROOM_RE =
  /^https?:\/\/(www\.)?airbnb\.(com(\.[a-z]{2})?|[a-z]{2})\/rooms\/(\d+)/i;

/** Returns true if the URL is a supported Airbnb listing URL. */
export function isValidAirbnbRoomUrl(url: string): boolean {
  return AIRBNB_ROOM_RE.test(url.trim());
}

/**
 * Parses a supported Airbnb listing URL.
 * Returns null if the URL is not a valid Airbnb listing URL.
 */
export function parseAirbnbRoomUrl(
  url: string
): { roomId: string; cleanedUrl: string } | null {
  const m = AIRBNB_ROOM_RE.exec(url.trim());
  if (!m) return null;
  const roomId = m[4];
  return { roomId, cleanedUrl: `https://www.airbnb.com/rooms/${roomId}` };
}
