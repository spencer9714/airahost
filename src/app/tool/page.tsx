"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import type {
  PropertyType,
  Amenity,
  DiscountStackingMode,
} from "@/lib/schemas";

const PROPERTY_TYPES: { value: PropertyType; label: string }[] = [
  { value: "entire_home", label: "Entire home" },
  { value: "private_room", label: "Private room" },
  { value: "shared_room", label: "Shared room" },
  { value: "hotel_room", label: "Hotel room" },
];

const AMENITY_OPTIONS: { value: Amenity; label: string }[] = [
  { value: "wifi", label: "WiFi" },
  { value: "kitchen", label: "Kitchen" },
  { value: "washer", label: "Washer" },
  { value: "dryer", label: "Dryer" },
  { value: "ac", label: "A/C" },
  { value: "heating", label: "Heating" },
  { value: "pool", label: "Pool" },
  { value: "hot_tub", label: "Hot tub" },
  { value: "free_parking", label: "Free parking" },
  { value: "ev_charger", label: "EV charger" },
  { value: "gym", label: "Gym" },
  { value: "bbq", label: "BBQ" },
  { value: "fire_pit", label: "Fire pit" },
  { value: "piano", label: "Piano" },
  { value: "lake_access", label: "Lake access" },
  { value: "ski_in_out", label: "Ski-in/out" },
  { value: "beach_access", label: "Beach access" },
];

function getDefaultDates() {
  const start = new Date();
  start.setDate(start.getDate() + 7);
  const end = new Date(start);
  end.setDate(end.getDate() + 30);
  return {
    startDate: start.toISOString().split("T")[0],
    endDate: end.toISOString().split("T")[0],
  };
}

export default function ToolPage() {
  const router = useRouter();
  const defaults = getDefaultDates();

  // Step tracking
  const [step, setStep] = useState(1);

  // Step 1 — Listing
  const [address, setAddress] = useState("");
  const [propertyType, setPropertyType] = useState<PropertyType>("entire_home");
  const [bedrooms, setBedrooms] = useState(1);
  const [bathrooms, setBathrooms] = useState(1);
  const [maxGuests, setMaxGuests] = useState(2);
  const [showAdvanced1, setShowAdvanced1] = useState(false);
  const [sizeSqFt, setSizeSqFt] = useState<number | undefined>();
  const [amenities, setAmenities] = useState<Amenity[]>([]);

  // Step 2 — Dates
  const [startDate, setStartDate] = useState(defaults.startDate);
  const [endDate, setEndDate] = useState(defaults.endDate);

  // Step 3 — Revenue Strategy
  const [weeklyDiscount, setWeeklyDiscount] = useState(10);
  const [monthlyDiscount, setMonthlyDiscount] = useState(20);
  const [refundable, setRefundable] = useState(true);
  const [nonRefundableDiscount, setNonRefundableDiscount] = useState(10);
  const [showAdvanced3, setShowAdvanced3] = useState(false);
  const [stackingMode, setStackingMode] =
    useState<DiscountStackingMode>("compound");
  const [maxTotalDiscount, setMaxTotalDiscount] = useState(40);

  // Submit
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  function toggleAmenity(a: Amenity) {
    setAmenities((prev) =>
      prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a]
    );
  }

  async function handleSubmit() {
    setLoading(true);
    setError("");

    try {
      const res = await fetch("/api/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          listing: {
            address,
            propertyType,
            bedrooms,
            bathrooms,
            maxGuests,
            sizeSqFt: sizeSqFt || undefined,
            amenities,
          },
          dates: { startDate, endDate },
          discountPolicy: {
            weeklyDiscountPct: weeklyDiscount,
            monthlyDiscountPct: monthlyDiscount,
            refundable,
            nonRefundableDiscountPct: nonRefundableDiscount,
            stackingMode,
            maxTotalDiscountPct: maxTotalDiscount,
          },
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Something went wrong");
      }

      const data = await res.json();
      router.push(`/r/${data.shareId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  const dateRange = Math.round(
    (new Date(endDate).getTime() - new Date(startDate).getTime()) /
      (1000 * 60 * 60 * 24)
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="mb-2 text-3xl font-bold">Analyze your listing</h1>
      <p className="mb-8 text-muted">
        Tell us about your property and pricing strategy.
      </p>

      <div className="flex flex-col gap-8 lg:flex-row">
        {/* Left — Form */}
        <div className="flex-1 space-y-6">
          {/* Step 1 */}
          <Card
            className={step === 1 ? "ring-2 ring-accent/20" : "opacity-80"}
          >
            <button
              className="mb-4 flex w-full items-center gap-3 text-left"
              onClick={() => setStep(1)}
            >
              <StepBadge n={1} active={step === 1} done={step > 1} />
              <span className="text-lg font-semibold">Your listing</span>
            </button>

            {step === 1 && (
              <div className="space-y-5">
                <Field label="Address">
                  <input
                    type="text"
                    placeholder="123 Main St, City, State"
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                    className="input"
                  />
                </Field>

                <Field label="Property type">
                  <div className="flex flex-wrap gap-2">
                    {PROPERTY_TYPES.map((pt) => (
                      <button
                        key={pt.value}
                        onClick={() => setPropertyType(pt.value)}
                        className={`rounded-xl border px-4 py-2 text-sm transition-all ${
                          propertyType === pt.value
                            ? "border-accent bg-accent/5 text-accent"
                            : "border-border hover:border-foreground/30"
                        }`}
                      >
                        {pt.label}
                      </button>
                    ))}
                  </div>
                </Field>

                <div className="grid grid-cols-3 gap-4">
                  <Field label="Bedrooms">
                    <Stepper
                      value={bedrooms}
                      onChange={setBedrooms}
                      min={0}
                      max={20}
                    />
                  </Field>
                  <Field label="Bathrooms">
                    <Stepper
                      value={bathrooms}
                      onChange={setBathrooms}
                      min={0.5}
                      max={20}
                      step={0.5}
                    />
                  </Field>
                  <Field label="Max guests">
                    <Stepper
                      value={maxGuests}
                      onChange={setMaxGuests}
                      min={1}
                      max={50}
                    />
                  </Field>
                </div>

                <button
                  className="text-sm text-muted underline"
                  onClick={() => setShowAdvanced1(!showAdvanced1)}
                >
                  {showAdvanced1 ? "Hide" : "Show"} advanced options
                </button>

                {showAdvanced1 && (
                  <div className="space-y-4 rounded-xl bg-gray-50 p-4">
                    <Field label="Size (sq ft)">
                      <input
                        type="number"
                        placeholder="Optional"
                        value={sizeSqFt ?? ""}
                        onChange={(e) =>
                          setSizeSqFt(
                            e.target.value ? Number(e.target.value) : undefined
                          )
                        }
                        className="input"
                      />
                    </Field>
                    <Field label="Amenities">
                      <div className="flex flex-wrap gap-2">
                        {AMENITY_OPTIONS.map((a) => (
                          <button
                            key={a.value}
                            onClick={() => toggleAmenity(a.value)}
                            className={`rounded-full border px-3 py-1.5 text-xs transition-all ${
                              amenities.includes(a.value)
                                ? "border-accent bg-accent/5 text-accent"
                                : "border-border hover:border-foreground/30"
                            }`}
                          >
                            {a.label}
                          </button>
                        ))}
                      </div>
                    </Field>
                  </div>
                )}

                <Button
                  onClick={() => setStep(2)}
                  disabled={address.length < 5}
                  className="w-full"
                >
                  Continue
                </Button>
              </div>
            )}
          </Card>

          {/* Step 2 */}
          <Card
            className={step === 2 ? "ring-2 ring-accent/20" : "opacity-80"}
          >
            <button
              className="mb-4 flex w-full items-center gap-3 text-left"
              onClick={() => step > 1 && setStep(2)}
            >
              <StepBadge n={2} active={step === 2} done={step > 2} />
              <span className="text-lg font-semibold">Dates</span>
            </button>

            {step === 2 && (
              <div className="space-y-5">
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Start date">
                    <input
                      type="date"
                      value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                      className="input"
                    />
                  </Field>
                  <Field label="End date">
                    <input
                      type="date"
                      value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                      className="input"
                    />
                  </Field>
                </div>
                <p className="text-sm text-muted">
                  {dateRange > 0
                    ? `${dateRange} nights selected (max 180)`
                    : "Please select valid dates"}
                </p>
                <Button
                  onClick={() => setStep(3)}
                  disabled={dateRange < 1 || dateRange > 180}
                  className="w-full"
                >
                  Continue
                </Button>
              </div>
            )}
          </Card>

          {/* Step 3 */}
          <Card
            className={step === 3 ? "ring-2 ring-accent/20" : "opacity-80"}
          >
            <button
              className="mb-4 flex w-full items-center gap-3 text-left"
              onClick={() => step > 2 && setStep(3)}
            >
              <StepBadge n={3} active={step === 3} done={false} />
              <span className="text-lg font-semibold">Revenue strategy</span>
            </button>

            {step === 3 && (
              <div className="space-y-5">
                <Field label={`Weekly discount: ${weeklyDiscount}%`}>
                  <input
                    type="range"
                    min={0}
                    max={50}
                    value={weeklyDiscount}
                    onChange={(e) => setWeeklyDiscount(Number(e.target.value))}
                    className="w-full accent-accent"
                  />
                </Field>
                <Field label={`Monthly discount: ${monthlyDiscount}%`}>
                  <input
                    type="range"
                    min={0}
                    max={70}
                    value={monthlyDiscount}
                    onChange={(e) => setMonthlyDiscount(Number(e.target.value))}
                    className="w-full accent-accent"
                  />
                </Field>

                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">
                    Refundable cancellation
                  </span>
                  <button
                    onClick={() => setRefundable(!refundable)}
                    className={`relative h-7 w-12 rounded-full transition-colors ${
                      refundable ? "bg-accent" : "bg-gray-300"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-transform ${
                        refundable ? "left-[22px]" : "left-0.5"
                      }`}
                    />
                  </button>
                </div>

                {!refundable && (
                  <Field
                    label={`Non-refundable discount: ${nonRefundableDiscount}%`}
                  >
                    <input
                      type="range"
                      min={0}
                      max={30}
                      value={nonRefundableDiscount}
                      onChange={(e) =>
                        setNonRefundableDiscount(Number(e.target.value))
                      }
                      className="w-full accent-accent"
                    />
                  </Field>
                )}

                <button
                  className="text-sm text-muted underline"
                  onClick={() => setShowAdvanced3(!showAdvanced3)}
                >
                  {showAdvanced3 ? "Hide" : "Show"} advanced options
                </button>

                {showAdvanced3 && (
                  <div className="space-y-4 rounded-xl bg-gray-50 p-4">
                    <Field label="Discount stacking mode">
                      <div className="flex flex-wrap gap-2">
                        {(
                          [
                            {
                              value: "compound" as const,
                              label: "Compound",
                              desc: "Discounts multiply",
                            },
                            {
                              value: "best_only" as const,
                              label: "Best only",
                              desc: "Largest wins",
                            },
                            {
                              value: "additive" as const,
                              label: "Additive",
                              desc: "Discounts add up",
                            },
                          ] as const
                        ).map((m) => (
                          <button
                            key={m.value}
                            onClick={() => setStackingMode(m.value)}
                            className={`rounded-xl border px-4 py-2 text-sm transition-all ${
                              stackingMode === m.value
                                ? "border-accent bg-accent/5 text-accent"
                                : "border-border hover:border-foreground/30"
                            }`}
                          >
                            <span className="font-medium">{m.label}</span>
                            <span className="ml-1 text-xs text-muted">
                              ({m.desc})
                            </span>
                          </button>
                        ))}
                      </div>
                    </Field>
                    <Field
                      label={`Max total discount cap: ${maxTotalDiscount}%`}
                    >
                      <input
                        type="range"
                        min={0}
                        max={80}
                        value={maxTotalDiscount}
                        onChange={(e) =>
                          setMaxTotalDiscount(Number(e.target.value))
                        }
                        className="w-full accent-accent"
                      />
                    </Field>
                  </div>
                )}

                {error && (
                  <p className="rounded-xl bg-red-50 p-3 text-sm text-warning">
                    {error}
                  </p>
                )}

                <Button
                  onClick={handleSubmit}
                  disabled={loading}
                  className="w-full"
                  size="lg"
                >
                  {loading ? "Generating..." : "Generate Revenue Report"}
                </Button>
              </div>
            )}
          </Card>
        </div>

        {/* Right — Sticky Preview */}
        <div className="hidden lg:block lg:w-80">
          <div className="sticky top-24">
            <Card>
              <h3 className="mb-4 text-lg font-semibold">Your listing</h3>
              <div className="space-y-3 text-sm">
                <SummaryRow
                  label="Address"
                  value={address || "Not entered yet"}
                />
                <SummaryRow
                  label="Type"
                  value={
                    PROPERTY_TYPES.find((p) => p.value === propertyType)
                      ?.label ?? ""
                  }
                />
                <SummaryRow
                  label="Bedrooms"
                  value={String(bedrooms)}
                />
                <SummaryRow
                  label="Bathrooms"
                  value={String(bathrooms)}
                />
                <SummaryRow
                  label="Guests"
                  value={String(maxGuests)}
                />
                {amenities.length > 0 && (
                  <SummaryRow
                    label="Amenities"
                    value={`${amenities.length} selected`}
                  />
                )}
                <div className="my-3 border-t border-border" />
                <SummaryRow
                  label="Dates"
                  value={dateRange > 0 ? `${dateRange} nights` : "—"}
                />
                <SummaryRow
                  label="Weekly discount"
                  value={`${weeklyDiscount}%`}
                />
                <SummaryRow
                  label="Monthly discount"
                  value={`${monthlyDiscount}%`}
                />
                <SummaryRow
                  label="Cancellation"
                  value={refundable ? "Refundable" : "Non-refundable"}
                />
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium">{label}</label>
      {children}
    </div>
  );
}

function Stepper({
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => onChange(Math.max(min, value - step))}
        className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-lg transition-colors hover:border-foreground/40"
        disabled={value <= min}
      >
        -
      </button>
      <span className="w-8 text-center font-medium">{value}</span>
      <button
        onClick={() => onChange(Math.min(max, value + step))}
        className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-lg transition-colors hover:border-foreground/40"
        disabled={value >= max}
      >
        +
      </button>
    </div>
  );
}

function StepBadge({
  n,
  active,
  done,
}: {
  n: number;
  active: boolean;
  done: boolean;
}) {
  if (done) {
    return (
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-success text-sm font-bold text-white">
        ✓
      </div>
    );
  }
  return (
    <div
      className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold ${
        active
          ? "bg-accent text-white"
          : "border border-border text-muted"
      }`}
    >
      {n}
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
