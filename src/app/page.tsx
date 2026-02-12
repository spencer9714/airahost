import Link from "next/link";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";

const steps = [
  {
    number: "1",
    title: "Describe your listing",
    description:
      "Enter your address, property details, and amenities. No Airbnb login needed.",
  },
  {
    number: "2",
    title: "Set your pricing strategy",
    description:
      "Choose your date range and discount preferences — weekly, monthly, and refundable options.",
  },
  {
    number: "3",
    title: "Get your revenue report",
    description:
      "See your market position, daily pricing calendar, and actionable revenue insights.",
  },
];

export default function LandingPage() {
  return (
    <div className="mx-auto max-w-5xl px-6">
      {/* Hero */}
      <section className="py-20 text-center md:py-28">
        <h1 className="text-4xl font-bold tracking-tight md:text-5xl lg:text-6xl">
          Your Airbnb Revenue Coach
        </h1>
        <p className="mx-auto mt-6 max-w-xl text-lg text-muted md:text-xl">
          Understand your market. Price smarter. Earn more.
        </p>
        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <Link href="/tool">
            <Button size="lg">Analyze my listing</Button>
          </Link>
          <Link href="/r/demo">
            <Button variant="ghost" size="lg">
              View sample report
            </Button>
          </Link>
        </div>
        <p className="mt-8 text-sm text-muted">
          Free. No Airbnb login required.
        </p>
      </section>

      {/* How it works */}
      <section className="pb-20">
        <h2 className="mb-10 text-center text-2xl font-semibold">
          How it works
        </h2>
        <div className="grid gap-6 md:grid-cols-3">
          {steps.map((step) => (
            <Card key={step.number}>
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-accent/10 text-sm font-bold text-accent">
                {step.number}
              </div>
              <h3 className="mb-2 text-lg font-semibold">{step.title}</h3>
              <p className="text-sm leading-relaxed text-muted">
                {step.description}
              </p>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
