/**
 * POST /api/internal/forecast/listing/[listingId]
 *
 * DEPRECATED — forecast_snapshot is no longer part of the product.
 * This endpoint is permanently disabled and returns 410 Gone.
 *
 * Historical forecast_snapshot rows are preserved in the database but
 * no new ones will be created.  The scheduler should be unconfigured.
 */

import { NextResponse } from "next/server";

export async function POST() {
  return NextResponse.json(
    {
      error: "gone",
      message:
        "The forecast_snapshot pipeline has been removed. Remove this route from the scheduler.",
    },
    { status: 410 }
  );
}
