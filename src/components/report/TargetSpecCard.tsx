import { Card } from "@/components/Card";
import type { TargetSpec } from "@/lib/schemas";

const PROPERTY_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  entire_home: { label: "Entire home", color: "bg-emerald-50 text-emerald-700" },
  private_room: { label: "Private room", color: "bg-blue-50 text-blue-700" },
  shared_room: { label: "Shared room", color: "bg-amber-50 text-amber-700" },
  hotel_room: { label: "Hotel room", color: "bg-purple-50 text-purple-700" },
};

const AMENITY_LABELS: Record<string, string> = {
  wifi: "WiFi",
  kitchen: "Kitchen",
  washer: "Washer",
  dryer: "Dryer",
  ac: "A/C",
  heating: "Heating",
  pool: "Pool",
  hot_tub: "Hot tub",
  free_parking: "Free parking",
  ev_charger: "EV charger",
  gym: "Gym",
  bbq: "BBQ",
  fire_pit: "Fire pit",
  piano: "Piano",
  lake_access: "Lake access",
  ski_in_out: "Ski-in/out",
  beach_access: "Beach access",
};

export function TargetSpecCard({ spec }: { spec: TargetSpec }) {
  const pt = PROPERTY_TYPE_LABELS[spec.propertyType] ?? {
    label: spec.propertyType || "Unknown",
    color: "bg-gray-100 text-gray-700",
  };

  return (
    <Card>
      <h3 className="mb-3 text-sm font-semibold text-muted">Your listing</h3>

      {spec.title && (
        <p className="mb-2 text-base font-semibold">{spec.title}</p>
      )}

      <span
        className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${pt.color}`}
      >
        {pt.label}
      </span>

      {spec.location && (
        <p className="mt-2 text-sm text-muted">{spec.location}</p>
      )}

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <p className="text-xs text-muted">Guests</p>
          <p className="text-sm font-semibold">
            {spec.accommodates ?? "—"}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted">Bedrooms</p>
          <p className="text-sm font-semibold">{spec.bedrooms ?? "—"}</p>
        </div>
        <div>
          <p className="text-xs text-muted">Beds</p>
          <p className="text-sm font-semibold">{spec.beds ?? "—"}</p>
        </div>
        <div>
          <p className="text-xs text-muted">Baths</p>
          <p className="text-sm font-semibold">{spec.baths ?? "—"}</p>
        </div>
      </div>

      {spec.amenities.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {spec.amenities.map((a) => (
            <span
              key={a}
              className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600"
            >
              {AMENITY_LABELS[a] ?? a}
            </span>
          ))}
        </div>
      )}

      {spec.rating != null && (
        <p className="mt-3 text-sm text-muted">
          <span className="font-medium text-foreground">{spec.rating}</span>
          {spec.reviews != null && (
            <span> ({spec.reviews.toLocaleString()} reviews)</span>
          )}
        </p>
      )}
    </Card>
  );
}
