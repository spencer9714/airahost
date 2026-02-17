"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SliderField } from "@/components/ui/SliderField";
import type {
  CalendarDay,
  LastMinuteStrategyMode,
  LastMinuteStrategyPreference,
} from "@/lib/schemas";

const DEFAULT_STRATEGY: LastMinuteStrategyPreference = {
  mode: "auto",
  aggressiveness: 50,
  floor: 0.65,
  cap: 1.05,
};
const PREVIEW_CAP = 1.05;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function formatPct(value: number): string {
  return `${value > 0 ? "+" : ""}${value}%`;
}

export function LastMinuteStrategyCard({
  reportId,
  calendar,
  isSignedIn,
}: {
  reportId: string;
  calendar: CalendarDay[];
  isSignedIn: boolean | null;
}) {
  const [mode, setMode] = useState<LastMinuteStrategyMode>(DEFAULT_STRATEGY.mode);
  const [aggressiveness, setAggressiveness] = useState(DEFAULT_STRATEGY.aggressiveness);
  const [floor, setFloor] = useState(DEFAULT_STRATEGY.floor);
  const [loadingPref, setLoadingPref] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadPreference() {
      if (isSignedIn !== true || !reportId || reportId === "demo") return;
      setLoadingPref(true);
      setError("");
      try {
        const res = await fetch(`/api/reports/${reportId}/strategy`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error("Failed to load strategy.");
        const data = (await res.json()) as {
          strategy?: LastMinuteStrategyPreference;
        };
        const pref = data.strategy ?? DEFAULT_STRATEGY;
        if (!cancelled) {
          setMode(pref.mode);
          setAggressiveness(pref.aggressiveness);
          setFloor(pref.floor);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoadingPref(false);
      }
    }
    loadPreference();
    return () => {
      cancelled = true;
    };
  }, [isSignedIn, reportId]);

  const next14 = useMemo(() => calendar.slice(0, 14), [calendar]);

  const autoRange = useMemo(() => {
    const multipliers = next14
      .map((d) => d.dynamicAdjustment?.finalMultiplier)
      .filter((v): v is number => typeof v === "number");
    if (!multipliers.length) return null;
    const minPct = Math.round((Math.min(...multipliers) - 1) * 100);
    const maxPct = Math.round((Math.max(...multipliers) - 1) * 100);
    return { minPct, maxPct };
  }, [next14]);

  const customPreview = useMemo(() => {
    const k = (aggressiveness - 50) / 50;
    const strength = 0.1 * (1 + 0.6 * k);
    const rows = next14
      .map((day) => {
        const demandScore = day.dynamicAdjustment?.demandScore;
        const timeMultiplier = day.dynamicAdjustment?.timeMultiplier;
        const baseDailyPrice = day.baseDailyPrice;
        if (
          demandScore == null ||
          timeMultiplier == null ||
          baseDailyPrice == null
        ) {
          return null;
        }
        const demandAdjustment = clamp(
          1 - (0.6 - demandScore) * strength,
          0.9,
          1.05
        );
        const finalMultiplier = clamp(
          timeMultiplier * demandAdjustment,
          floor,
          PREVIEW_CAP
        );
        return {
          baseDailyPrice,
          adjustedPrice: Math.round(baseDailyPrice * finalMultiplier),
          finalMultiplier,
        };
      })
      .filter((v): v is NonNullable<typeof v> => v !== null);

    if (!rows.length) return null;
    const multipliers = rows.map((r) => r.finalMultiplier);
    const minPct = Math.round((Math.min(...multipliers) - 1) * 100);
    const maxPct = Math.round((Math.max(...multipliers) - 1) * 100);
    const avgBase = Math.round(
      rows.reduce((sum, r) => sum + r.baseDailyPrice, 0) / rows.length
    );
    const avgAdjusted = Math.round(
      rows.reduce((sum, r) => sum + r.adjustedPrice, 0) / rows.length
    );
    return { minPct, maxPct, avgBase, avgAdjusted };
  }, [next14, aggressiveness, floor]);

  async function saveStrategy() {
    if (isSignedIn !== true || !reportId || reportId === "demo") return;
    setSaving(true);
    setError("");
    try {
      const res = await fetch(`/api/reports/${reportId}/strategy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          aggressiveness,
          floor: Math.min(floor, 0.9),
          cap: 1.05,
        } satisfies LastMinuteStrategyPreference),
      });
      if (!res.ok) throw new Error("Failed to save strategy.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="mb-8">
      <h2 className="mb-4 text-lg font-semibold">Last-minute pricing strategy</h2>
      <Card>
        <SegmentedControl
          value={mode}
          onChange={setMode}
          options={[
            { label: "Auto (Recommended)", value: "auto" },
            { label: "Customize", value: "manual" },
          ]}
        />

        {mode === "auto" ? (
          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <p className="text-sm text-muted">
              We automatically adjust your prices as dates approach based on
              demand and booking pace.
            </p>
            <p className="mt-2 text-sm">
              {autoRange
                ? `Typical adjustment: ${formatPct(autoRange.minPct)} to ${formatPct(autoRange.maxPct)} in the next 14 days`
                : "Typical adjustment unavailable for this report."}
            </p>
          </div>
        ) : (
          <div className="mt-4 space-y-4 rounded-xl border border-gray-100 bg-gray-50 p-4">
            <SliderField
              label="Discount aggressiveness"
              value={aggressiveness}
              min={0}
              max={100}
              displayValue={`${aggressiveness}`}
              helperText="Higher = larger last-minute discounts if dates remain unbooked"
              onChange={setAggressiveness}
            />
            <SliderField
              label="Minimum price floor"
              value={floor}
              min={0.65}
              max={0.95}
              step={0.01}
              displayValue={floor.toFixed(2)}
              helperText="Never discount below this level"
              onChange={(v) => setFloor(Number(v.toFixed(2)))}
            />
            <p className="text-xs text-muted">
              Preview shows last-minute adjustment only.
            </p>
            {customPreview ? (
              <p className="text-sm">
                Typical adjustment: {formatPct(customPreview.minPct)} to{" "}
                {formatPct(customPreview.maxPct)} in the next 14 days. Preview
                nightly average: ${customPreview.avgAdjusted} (base $
                {customPreview.avgBase}).
              </p>
            ) : (
              <p className="text-sm text-muted">Preview unavailable for this report.</p>
            )}
          </div>
        )}

        <div className="mt-4 flex items-center gap-3">
          {isSignedIn === true ? (
            <Button size="sm" onClick={saveStrategy} disabled={saving || loadingPref}>
              {saving ? "Saving..." : "Save strategy to dashboard"}
            </Button>
          ) : (
            <Link href="/login?next=/dashboard" className="text-sm text-accent hover:underline">
              Sign in to save your pricing strategy
            </Link>
          )}
        </div>
        {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
      </Card>
    </section>
  );
}
