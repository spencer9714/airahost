import { NextRequest, NextResponse } from "next/server";

/**
 * GET /api/benchmark-title?url=<airbnb-url>
 *
 * Fetches the Airbnb listing page server-side and extracts the listing title
 * from og:title (preferred) or the <title> tag (fallback).
 *
 * Returns { title: string | null, error?: string }
 */
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const url = searchParams.get("url");

  if (!url || !url.includes("airbnb.com/rooms/")) {
    return NextResponse.json(
      { title: null, error: "Invalid or missing Airbnb rooms URL" },
      { status: 400 }
    );
  }

  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        Accept:
          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
      },
      // Timeout via AbortSignal
      signal: AbortSignal.timeout(8000),
    });

    if (!res.ok) {
      return NextResponse.json(
        { title: null, error: `Airbnb returned status ${res.status}` },
        { status: 200 }
      );
    }

    // Only read first 32 KB — the og:title and title tags are always in <head>
    const reader = res.body?.getReader();
    if (!reader) {
      return NextResponse.json({ title: null, error: "No response body" });
    }

    let html = "";
    const decoder = new TextDecoder("utf-8");
    let done = false;
    while (!done && html.length < 32768) {
      const chunk = await reader.read();
      done = chunk.done;
      if (chunk.value) {
        html += decoder.decode(chunk.value, { stream: !done });
      }
    }
    reader.cancel().catch(() => undefined);

    const title = extractTitle(html);
    return NextResponse.json({ title });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ title: null, error: msg }, { status: 200 });
  }
}

/**
 * Try og:title first, then <title>, strip common Airbnb suffixes.
 */
function extractTitle(html: string): string | null {
  // 1. og:title — most reliable for Airbnb
  const ogMatch = html.match(
    /<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i
  ) ?? html.match(
    /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title["']/i
  );

  if (ogMatch?.[1]) {
    return cleanTitle(ogMatch[1]);
  }

  // 2. <title> fallback
  const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
  if (titleMatch?.[1]) {
    return cleanTitle(titleMatch[1]);
  }

  return null;
}

function cleanTitle(raw: string): string {
  return raw
    .trim()
    // Decode common HTML entities
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    // Strip Airbnb suffixes
    .replace(/\s*[-|]\s*Airbnb\s*$/i, "")
    .replace(/\s*\|\s*Vacation Rentals\s*$/i, "")
    .trim() || "";
}
