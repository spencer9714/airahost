"use client";

import React, { useState } from "react";
import { AutoApplyDrawer } from "./AutoApplyDrawer";
import { AutoApplyPreviewPanel } from "./AutoApplyPreviewPanel";
import type { AutoApplySettings } from "./AutoApplyDrawer";
import type { CalendarDay } from "@/lib/schemas";
import type { CohostVerificationStatus } from "@/lib/schemas";
import { computeAutoApplyPreview } from "@/lib/autoApplyPreview";
import type { AutoApplyPreviewResult } from "@/lib/autoApplyPreview";
import { extractAirbnbListingId } from "@/lib/airbnb-utils";

// ── Types ──────────────────────────────────────────────────────────────────

interface AutoApplyListingData {
  id: string;
  name: string;
  input_attributes?: { listingUrl?: string | null };
  // Co-host verification model (migration 020)
  auto_apply_cohost_status?: string;
  auto_apply_cohost_verification_error?: string | null;
  // Auto-Apply settings
  auto_apply_enabled?: boolean;
  auto_apply_window_end_days?: number;
  auto_apply_scope?: "actionable" | "all_sellable";
  auto_apply_min_price_floor?: number | null;
  auto_apply_min_notice_days?: number;
  auto_apply_max_increase_pct?: number | null;
  auto_apply_max_decrease_pct?: number | null;
  auto_apply_skip_unavailable?: boolean;
  auto_apply_last_updated_at?: string | null;
}

interface AutoApplyFeatureProps {
  listing: AutoApplyListingData;
  calendar?: CalendarDay[];
  onSaveAutoApply: (
    listingId: string,
    patch: Partial<{
      autoApplyEnabled: boolean;
      autoApplyCohostInviteOpened: boolean;
      autoApplyWindowEndDays: number;
      autoApplyScope: "actionable" | "all_sellable";
      autoApplyMinPriceFloor: number | null;
      autoApplyMinNoticeDays: number;
      autoApplyMaxIncreasePct: number | null;
      autoApplyMaxDecreasePct: number | null;
      autoApplySkipUnavailable: boolean;
    }>
  ) => Promise<void>;
  /** Triggers POST /cohost-verify then refreshes listing data. */
  onTriggerCohostVerification: (listingId: string) => Promise<void>;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function settingsFromListing(listing: AutoApplyListingData): AutoApplySettings {
  return {
    enabled: listing.auto_apply_enabled ?? false,
    windowEndDays: Math.min(listing.auto_apply_window_end_days ?? 30, 30),
    applyScope: listing.auto_apply_scope ?? "actionable",
    minPriceFloor: listing.auto_apply_min_price_floor ?? null,
    minNoticeDays: listing.auto_apply_min_notice_days ?? 1,
    maxIncreasePct: listing.auto_apply_max_increase_pct ?? null,
    maxDecreasePct: listing.auto_apply_max_decrease_pct ?? null,
    skipUnavailableNights: listing.auto_apply_skip_unavailable ?? true,
    lastUpdatedAt: listing.auto_apply_last_updated_at ?? null,
  };
}

// ── Co-host state card sub-components ─────────────────────────────────────

/** Shared quiet row shown after the user dismisses the co-host prompt. */
function CohostDismissedRow({ onResume }: { onResume: () => void }) {
  return (
    <div
      className="flex items-center justify-between border-t border-gray-100/60 px-5 py-2.5"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground/30">Auto-Apply</span>
        <span className="text-[11px] text-foreground/22">· Co-host required</span>
      </div>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onResume(); }}
        className="text-[11px] text-foreground/30 underline underline-offset-2 transition-opacity hover:text-foreground/55"
      >
        Set up
      </button>
    </div>
  );
}

/** not_started: one action only — send the user to Airbnb */
function CohostNotStartedCard({
  onOpenAirbnb,
  onDismiss,
}: {
  onOpenAirbnb: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      className="border-t border-amber-100/80 bg-amber-50/40 px-5 py-3"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground/50">Auto-Apply</span>
        <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
          Co-host required
        </span>
      </div>
      <p className="text-[11px] text-amber-800/55">
        Add Airahost in Airbnb to continue.
      </p>
      <div className="mt-2.5 flex items-center gap-3">
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onOpenAirbnb(); }}
          className="text-xs font-semibold text-amber-700 underline underline-offset-2 transition-opacity hover:opacity-70"
        >
          Continue to Airbnb →
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDismiss(); }}
          className="text-[11px] text-amber-700/40 underline underline-offset-2 transition-opacity hover:opacity-70"
        >
          Not now
        </button>
      </div>
    </div>
  );
}

/** invite_opened: primary = confirm; secondary = not now */
function CohostInviteOpenedCard({
  saving,
  onConfirmed,
  onDismiss,
}: {
  saving: boolean;
  onConfirmed: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      className="border-t border-amber-100/80 bg-amber-50/40 px-5 py-3"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground/50">Auto-Apply</span>
        <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
          Co-host required
        </span>
      </div>
      <p className="text-[11px] text-amber-800/55">
        Finished in Airbnb?
      </p>
      <div className="mt-2.5 flex items-center gap-3">
        <button
          type="button"
          disabled={saving}
          onClick={(e) => { e.stopPropagation(); onConfirmed(); }}
          className="text-xs font-semibold text-amber-700 underline underline-offset-2 transition-opacity hover:opacity-70 disabled:opacity-40"
        >
          {saving ? "Saving…" : "I've added Airahost →"}
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDismiss(); }}
          className="text-[11px] text-amber-700/40 underline underline-offset-2 transition-opacity hover:opacity-70"
        >
          Not now
        </button>
      </div>
    </div>
  );
}

/** verification_pending / user_confirmed: waiting state */
function CohostPendingCard({
  verifyError,
  onEditSettings,
}: {
  verifyError: string | null;
  onEditSettings: () => void;
}) {
  return (
    <div
      className="cursor-pointer border-t border-blue-100/80 bg-blue-50/40 px-5 py-3 transition-colors hover:bg-blue-100/40"
      onClick={(e) => { e.stopPropagation(); onEditSettings(); }}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground/50">Auto-Apply</span>
        <span className="flex items-center gap-1 rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />
          Verifying access
        </span>
      </div>
      <p className="text-[11px] text-foreground/40">
        Confirming your co-host status with Airbnb.
      </p>
      {verifyError && (
        <p className="mt-1 text-[11px] text-rose-500/80">{verifyError}</p>
      )}
    </div>
  );
}

/** verification_failed: primary = retry; secondary = not now */
function CohostFailedCard({
  cohostInviteUrl,
  verificationError,
  saving,
  verifyError,
  onRetry,
  onDismiss,
}: {
  cohostInviteUrl: string;
  verificationError: string | null;
  saving: boolean;
  verifyError: string | null;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      className="border-t border-rose-100/80 bg-rose-50/30 px-5 py-3"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground/50">Auto-Apply</span>
        <span className="rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">
          Verification failed
        </span>
      </div>
      <p className="text-[11px] text-rose-800/55">
        We couldn&apos;t confirm access.
      </p>
      {(verificationError || verifyError) && (
        <p className="mt-1 text-[11px] text-rose-600/70">
          {verifyError ?? verificationError}
        </p>
      )}
      <div className="mt-2.5 flex items-center gap-3">
        <button
          type="button"
          disabled={saving}
          onClick={(e) => { e.stopPropagation(); onRetry(); }}
          className="text-xs font-semibold text-rose-700 underline underline-offset-2 transition-opacity hover:opacity-70 disabled:opacity-40"
        >
          {saving ? "Retrying…" : "Try again →"}
        </button>
        <a
          href={cohostInviteUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-rose-700/40 underline underline-offset-2 transition-opacity hover:opacity-70"
          onClick={(e) => e.stopPropagation()}
        >
          Manage in Airbnb
        </a>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDismiss(); }}
          className="text-[11px] text-rose-700/30 underline underline-offset-2 transition-opacity hover:opacity-70"
        >
          Not now
        </button>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function AutoApplyFeature({
  listing,
  calendar,
  onSaveAutoApply,
  onTriggerCohostVerification,
}: AutoApplyFeatureProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [draftPreview, setDraftPreview] = useState<AutoApplyPreviewResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  /** Local-only dismiss: user clicked "Not now" on a pre-verified prompt. */
  const [cohostDismissed, setCohostDismissed] = useState(false);

  const settings = settingsFromListing(listing);
  const isConfigured = settings.lastUpdatedAt !== null;

  const cohostStatus = (listing.auto_apply_cohost_status ?? "not_started") as CohostVerificationStatus;
  const isVerified = cohostStatus === "verified";
  const isOn = isConfigured && isVerified && settings.enabled;

  const rangeLabel =
    settings.windowEndDays <= 7
      ? "Next 7 nights"
      : settings.windowEndDays <= 14
      ? "Next 14 nights"
      : "Next 30 nights";

  const lastUpdatedLabel = settings.lastUpdatedAt
    ? new Date(settings.lastUpdatedAt).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : null;

  const listingUrl = listing.input_attributes?.listingUrl ?? null;
  const airbnbListingId = listingUrl ? extractAirbnbListingId(listingUrl) : null;
  const cohostInviteUrl = airbnbListingId
    ? `https://www.airbnb.com/hosting/listings/editor/${airbnbListingId}/details/co-hosts/invite`
    : "https://www.airbnb.com/hosting/listings";

  // ── Action handlers ──────────────────────────────────────────────────────

  async function handleToggle(e: React.MouseEvent) {
    e.stopPropagation();
    if (!isConfigured) { setDrawerOpen(true); return; }
    if (!isVerified) return;
    setSaving(true);
    try {
      await onSaveAutoApply(listing.id, { autoApplyEnabled: !settings.enabled });
    } finally {
      setSaving(false);
    }
  }

  async function handleSave(patch: Omit<AutoApplySettings, "enabled" | "lastUpdatedAt">) {
    setSaving(true);
    try {
      await onSaveAutoApply(listing.id, {
        // Only enable automation if co-host access is verified.
        autoApplyEnabled: isVerified,
        autoApplyWindowEndDays: patch.windowEndDays,
        autoApplyScope: patch.applyScope,
        autoApplyMinPriceFloor: patch.minPriceFloor,
        autoApplyMinNoticeDays: patch.minNoticeDays,
        autoApplyMaxIncreasePct: patch.maxIncreasePct,
        autoApplyMaxDecreasePct: patch.maxDecreasePct,
        autoApplySkipUnavailable: patch.skipUnavailableNights,
      });
    } finally {
      setSaving(false);
      setDrawerOpen(false);
    }
  }

  async function handleDisable() {
    setSaving(true);
    try {
      await onSaveAutoApply(listing.id, { autoApplyEnabled: false });
    } finally {
      setSaving(false);
    }
  }

  /** Called when user clicks "Continue to Airbnb →". Records invite_opened if not started. */
  function handleOpenAirbnb() {
    if (cohostStatus === "not_started") {
      // Fire-and-forget — don't block opening the Airbnb page.
      onSaveAutoApply(listing.id, { autoApplyCohostInviteOpened: true }).catch(() => {});
    }
    window.open(cohostInviteUrl, "_blank", "noopener,noreferrer");
  }

  /** Called when user clicks "I've added Airahost" or "Try verification again". */
  async function handleUserConfirmed() {
    setSaving(true);
    setVerifyError(null);
    try {
      await onTriggerCohostVerification(listing.id);
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Verification request failed.");
    } finally {
      setSaving(false);
    }
  }

  // ── Shared panels (rendered by all states) ───────────────────────────────

  const sharedPanels = (
    <>
      {drawerOpen && (
        <AutoApplyDrawer
          listingName={listing.name}
          settings={settings}
          calendar={calendar}
          onClose={() => setDrawerOpen(false)}
          onSave={handleSave}
          onDisable={handleDisable}
          onViewPreview={(preview) => {
            setDraftPreview(preview);
            setDrawerOpen(false);
            setPreviewOpen(true);
          }}
        />
      )}
      {previewOpen && (
        <AutoApplyPreviewPanel
          listingName={listing.name}
          preview={draftPreview ?? computeAutoApplyPreview(calendar ?? [], settings)}
          isUnsavedDraft={draftPreview !== null}
          onClose={() => { setPreviewOpen(false); setDraftPreview(null); }}
          onEditSettings={() => { setPreviewOpen(false); setDraftPreview(null); setDrawerOpen(true); }}
        />
      )}
    </>
  );

  // ── State: Setup required ─────────────────────────────────────────────
  if (!isConfigured) {
    return (
      <>
        <div
          className="flex cursor-pointer items-center gap-2 border-t border-accent/10 bg-accent/5 px-5 py-3 transition-colors hover:bg-accent/10"
          onClick={(e) => { e.stopPropagation(); setDrawerOpen(true); }}
        >
          <span className="text-sm font-medium text-foreground/50">Auto-Apply</span>
          <span className="flex items-center gap-1 rounded-full bg-accent/20 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
            <span className="h-1.5 w-1.5 rounded-full bg-accent/60" />
            Not configured
          </span>
        </div>
        {sharedPanels}
      </>
    );
  }

  // ── Dismissed: user clicked "Not now" — show quiet row for all pre-verified states ──
  const isPreVerified =
    cohostStatus === "not_started" ||
    cohostStatus === "invite_opened" ||
    cohostStatus === "user_confirmed" ||
    cohostStatus === "verification_pending" ||
    cohostStatus === "verification_failed";

  if (isPreVerified && cohostDismissed) {
    return (
      <>
        <CohostDismissedRow onResume={() => setCohostDismissed(false)} />
        {sharedPanels}
      </>
    );
  }

  // ── State: Co-host not started ───────────────────────────────────────
  if (cohostStatus === "not_started") {
    return (
      <>
        <CohostNotStartedCard
          onOpenAirbnb={handleOpenAirbnb}
          onDismiss={() => setCohostDismissed(true)}
        />
        {sharedPanels}
      </>
    );
  }

  // ── State: Invite opened ──────────────────────────────────────────────
  if (cohostStatus === "invite_opened") {
    return (
      <>
        <CohostInviteOpenedCard
          saving={saving}
          onConfirmed={handleUserConfirmed}
          onDismiss={() => setCohostDismissed(true)}
        />
        {sharedPanels}
      </>
    );
  }

  // ── State: Verification pending ───────────────────────────────────────
  if (cohostStatus === "user_confirmed" || cohostStatus === "verification_pending") {
    return (
      <>
        <CohostPendingCard
          verifyError={verifyError}
          onEditSettings={() => setDrawerOpen(true)}
        />
        {sharedPanels}
      </>
    );
  }

  // ── State: Verification failed ────────────────────────────────────────
  if (cohostStatus === "verification_failed") {
    return (
      <>
        <CohostFailedCard
          cohostInviteUrl={cohostInviteUrl}
          verificationError={listing.auto_apply_cohost_verification_error ?? null}
          saving={saving}
          verifyError={verifyError}
          onRetry={handleUserConfirmed}
          onDismiss={() => setCohostDismissed(true)}
        />
        {sharedPanels}
      </>
    );
  }

  // ── State: Verified — On or Off ───────────────────────────────────────
  return (
    <>
      <div
        className="flex items-start justify-between border-t border-gray-100/80 px-5 py-3 transition-colors"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Left: label + pill + summary + links */}
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex items-center gap-2">
            <span
              className={`text-sm font-semibold ${
                isOn ? "text-foreground/60" : "text-foreground/35"
              }`}
            >
              Auto-Apply
            </span>
            <span
              className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                isOn
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-gray-100 text-foreground/40"
              }`}
            >
              {isOn ? "On" : "Off"}
            </span>
          </div>

          <div className={isOn ? "opacity-70" : "opacity-40"}>
            <p className="text-[11px] leading-snug text-foreground/70">
              {rangeLabel}
              {settings.minPriceFloor != null
                ? ` · $${settings.minPriceFloor}/night min`
                : ""}
            </p>
            <p className="text-[11px] text-foreground/50">
              {settings.applyScope === "actionable"
                ? "Actionable nights only"
                : "All sellable nights"}
            </p>
            {lastUpdatedLabel && (
              <p className="mt-0.5 text-[10px] text-foreground/30">
                Updated {lastUpdatedLabel}
              </p>
            )}
          </div>

          <div className="mt-2 flex items-center gap-3">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); setDrawerOpen(true); }}
              className="text-[11px] font-medium text-foreground/35 underline underline-offset-2 transition-colors hover:text-foreground/65"
            >
              Edit settings
            </button>
          </div>
        </div>

        {/* Right: toggle */}
        <button
          type="button"
          role="switch"
          aria-checked={isOn}
          aria-label={isOn ? "Turn off Auto-Apply" : "Turn on Auto-Apply"}
          disabled={saving}
          onClick={handleToggle}
          className={`relative ml-3 mt-0.5 inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
            isOn ? "bg-emerald-500" : "bg-gray-200"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
              isOn ? "translate-x-4" : "translate-x-1"
            }`}
          />
        </button>
      </div>

      {sharedPanels}
    </>
  );
}
