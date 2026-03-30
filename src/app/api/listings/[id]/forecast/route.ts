/**
 * POST /api/listings/[id]/forecast
 *
 * DEPRECATED — forecast_snapshot is no longer part of the product.
 * This endpoint is permanently disabled and returns 410 Gone.
 *
 * Historical forecast_snapshot rows are preserved in the database but
 * no new ones will be created.  The product now operates exclusively on
 * live_analysis reports (nightly scheduled or manual/rerun).
 */

import { NextResponse } from "next/server";

export async function POST() {
  return NextResponse.json(
    {
      error: "gone",
      message:
        "The forecast_snapshot pipeline has been removed. The product now uses live_analysis reports only.",
    },
    { status: 410 }
  );
}
