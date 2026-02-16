import { Card } from "@/components/Card";
import type { QueryCriteria } from "@/lib/schemas";

export function QueryCriteriaCard({ criteria }: { criteria: QueryCriteria }) {
  const tol = criteria.tolerances ?? {
    bedrooms: 0,
    accommodates: 0,
    beds: 0,
    baths: 0,
  };

  return (
    <Card>
      <h3 className="mb-3 text-sm font-semibold text-muted">
        Search criteria used
      </h3>

      <dl className="space-y-2 text-sm">
        <div className="flex justify-between">
          <dt className="text-muted">Location basis</dt>
          <dd className="font-medium">{criteria.locationBasis || "—"}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted">Search guests</dt>
          <dd className="font-medium">{criteria.searchAdults ?? "—"}</dd>
        </div>
        {criteria.propertyTypeFilter && (
          <div className="flex justify-between">
            <dt className="text-muted">Property type filter</dt>
            <dd className="font-medium capitalize">
              {criteria.propertyTypeFilter.replace("_", " ")}
            </dd>
          </div>
        )}
        <div className="flex justify-between">
          <dt className="text-muted">Date range</dt>
          <dd className="font-medium">
            {criteria.checkin ?? "—"} to {criteria.checkout ?? "—"}
          </dd>
        </div>
      </dl>

      <div className="mt-4 rounded-xl bg-gray-50 p-3">
        <p className="mb-1 text-xs font-medium text-muted">
          Similarity tolerances
        </p>
        <p className="text-xs text-muted leading-relaxed">
          Bedrooms &plusmn;{tol.bedrooms}, Guests &plusmn;{tol.accommodates},
          Beds &plusmn;{tol.beds}, Baths &plusmn;{tol.baths}
        </p>
      </div>
    </Card>
  );
}
