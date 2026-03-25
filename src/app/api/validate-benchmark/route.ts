import { NextRequest, NextResponse } from "next/server";
import { parseAirbnbRoomUrl } from "@/lib/benchmarkUrl";

export async function GET(req: NextRequest) {
  const url = (req.nextUrl.searchParams.get("url") ?? "").trim();

  if (!url) {
    return NextResponse.json({ valid: false, reason: "empty" });
  }

  const parsed = parseAirbnbRoomUrl(url);
  if (!parsed) {
    return NextResponse.json({ valid: false, reason: "not_airbnb_room" });
  }

  return NextResponse.json({ valid: true, cleanedUrl: parsed.cleanedUrl, roomId: parsed.roomId });
}
