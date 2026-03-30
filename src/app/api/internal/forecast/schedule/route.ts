/**
 * POST /api/internal/forecast/schedule
 *
 * DEPRECATED — forecast_snapshot is no longer part of the product.
 * This endpoint is permanently disabled and returns 410 Gone.
 *
 * If this route is still configured in a Railway cron or similar scheduler,
 * remove it.  No new forecast_snapshot jobs will be created.
 */

import { NextResponse } from "next/server";

export async function POST() {
  return NextResponse.json(
    {
      error: "gone",
      message:
        "The forecast_snapshot scheduler has been removed. Unconfigure this cron job.",
    },
    { status: 410 }
  );
}
