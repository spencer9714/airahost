/**
 * Flush-exclusions endpoint — used by `navigator.sendBeacon` on
 * `pagehide` / `visibilitychange='hidden'` / `beforeunload`.
 *
 * Accepts a *delta* payload (not the final arrays).  This endpoint reads
 * the current excludedComps / preferredComps from the database, applies the
 * delta, and writes the merged result.  Doing the merge server-side avoids
 * clobbering concurrent edits from another tab — a problem we'd have if the
 * client sent the full arrays it last saw and we just blindly overwrote.
 *
 * `sendBeacon` only supports POST, so this endpoint exists alongside the
 * regular `PATCH /api/listings/[id]`.
 *
 * Returns 204 on success — the page is unloading and won't read the body.
 */

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { excludedCompSchema, preferredCompSchema } from "@/lib/schemas";
import { enrichListingInputAttributes } from "@/lib/normalizedLocation";
import { getSupabaseServer } from "@/lib/supabaseServer";
import type { ExcludedComp, PreferredComp } from "@/lib/schemas";

const flushDeltaSchema = z.object({
  excludeAdds: z.array(excludedCompSchema).max(200).optional(),
  promoteAdds: z.array(preferredCompSchema).max(20).optional(),
  promoteUnexcludeRoomIds: z.array(z.string().regex(/^\d+$/)).max(200).optional(),
});

function extractRoomId(url: unknown): string | null {
  if (typeof url !== "string") return null;
  const m = url.match(/\/rooms\/(\d+)/);
  return m ? m[1] : null;
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const supabase = await getSupabaseServer();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { data: currentListing } = await supabase
      .from("saved_listings")
      .select("id, input_address, input_attributes")
      .eq("id", id)
      .eq("user_id", user.id)
      .single();
    if (!currentListing) {
      return NextResponse.json({ error: "Listing not found" }, { status: 404 });
    }

    const body = await req.json().catch(() => null);
    const parsed = flushDeltaSchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    // Fast path: nothing to do.
    const hasDelta =
      (parsed.data.excludeAdds?.length ?? 0) > 0 ||
      (parsed.data.promoteAdds?.length ?? 0) > 0 ||
      (parsed.data.promoteUnexcludeRoomIds?.length ?? 0) > 0;
    if (!hasDelta) {
      return new NextResponse(null, { status: 204 });
    }

    // ── Server-side delta merge against fresh DB state ──────────────
    // Read the current arrays as they exist NOW in Postgres, not whatever
    // snapshot the client thought it had.  Then apply the delta on top.
    const currentAttrs =
      (currentListing.input_attributes as Record<string, unknown> | null) ?? {};
    const currentExcluded: ExcludedComp[] = Array.isArray(
      currentAttrs.excludedComps
    )
      ? (currentAttrs.excludedComps as ExcludedComp[])
      : [];
    const currentPreferred: PreferredComp[] = Array.isArray(
      currentAttrs.preferredComps
    )
      ? (currentAttrs.preferredComps as PreferredComp[])
      : [];

    // Apply promoteUnexcludeRoomIds: drop those roomIds from current excluded.
    const unexcludeSet = new Set(parsed.data.promoteUnexcludeRoomIds ?? []);
    const afterUnexclude = unexcludeSet.size
      ? currentExcluded.filter((ec) => !unexcludeSet.has(ec.roomId))
      : currentExcluded;

    // Apply excludeAdds: append, deduped by roomId.
    const existingExcludedIds = new Set(afterUnexclude.map((ec) => ec.roomId));
    const newExcludeAdds = (parsed.data.excludeAdds ?? []).filter(
      (ec) => !existingExcludedIds.has(ec.roomId)
    );
    const nextExcluded = [...afterUnexclude, ...newExcludeAdds];

    // Apply promoteAdds: append, deduped by listingUrl (case-insensitive,
    // strip query string).
    const norm = (u: string) => u.split("?")[0].toLowerCase();
    const existingPreferredUrls = new Set(
      currentPreferred.map((pc) => norm(pc.listingUrl))
    );
    const newPromoteAdds = (parsed.data.promoteAdds ?? []).filter(
      (pc) => !existingPreferredUrls.has(norm(pc.listingUrl))
    );
    const nextPreferred = [...currentPreferred, ...newPromoteAdds];

    // ── Cross-field guard on the merged final state ─────────────────
    const finalExcludedRoomIds = new Set(nextExcluded.map((ec) => ec.roomId));
    if (finalExcludedRoomIds.size > 0 && nextPreferred.length > 0) {
      const conflictingIds: string[] = [];
      for (const pc of nextPreferred) {
        const rid = extractRoomId(pc.listingUrl);
        if (rid && finalExcludedRoomIds.has(rid)) conflictingIds.push(rid);
      }
      if (conflictingIds.length > 0) {
        return NextResponse.json(
          {
            error: "Cannot exclude a comp that is currently a benchmark",
            conflictingIds: Array.from(new Set(conflictingIds)),
          },
          { status: 400 }
        );
      }
    }

    // ── Compose updated input_attributes (preserve all other fields) ──
    let nextInputAttributes: Record<string, unknown> = { ...currentAttrs };
    if (nextExcluded.length > 0) {
      nextInputAttributes.excludedComps = nextExcluded;
    } else {
      const { excludedComps: _r, ...rest } = nextInputAttributes;
      void _r;
      nextInputAttributes = rest;
    }
    if (nextPreferred.length > 0) {
      nextInputAttributes.preferredComps = nextPreferred;
    } else {
      const { preferredComps: _r, ...rest } = nextInputAttributes;
      void _r;
      nextInputAttributes = rest;
    }

    const updates = {
      input_attributes: enrichListingInputAttributes(
        nextInputAttributes,
        currentListing.input_address ?? ""
      ),
    };
    const { error } = await supabase
      .from("saved_listings")
      .update(updates)
      .eq("id", id)
      .eq("user_id", user.id);
    if (error) {
      return NextResponse.json(
        { error: "Failed to flush exclusions" },
        { status: 500 }
      );
    }

    return new NextResponse(null, { status: 204 });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
