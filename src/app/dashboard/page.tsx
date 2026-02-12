"use client";

import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import Link from "next/link";

export default function DashboardPage() {
  // In Phase A, dashboard shows a placeholder.
  // Supabase auth integration will power this in Phase B.
  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="mb-2 text-3xl font-bold">Your reports</h1>
      <p className="mb-8 text-muted">
        View and manage your past revenue reports.
      </p>

      <Card className="py-12 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-gray-100 text-2xl">
          📊
        </div>
        <h2 className="mb-2 text-lg font-semibold">No reports yet</h2>
        <p className="mx-auto mb-6 max-w-sm text-sm text-muted">
          Create your first revenue report to see your pricing insights and
          market data here.
        </p>
        <Link href="/tool">
          <Button>Analyze my listing</Button>
        </Link>
      </Card>

      <div className="mt-8">
        <Card>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
            Coming soon
          </h3>
          <ul className="space-y-2 text-sm text-muted">
            <li>Save and compare multiple reports</li>
            <li>Track pricing trends over time</li>
            <li>Export data to spreadsheets</li>
            <li>Multi-listing portfolio view</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
