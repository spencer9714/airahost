import type { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
}

export function Card({ children, className = "" }: CardProps) {
  return (
    <div
      className={`rounded-2xl border border-border bg-white p-4 shadow-[var(--card-shadow)] sm:p-6 ${className}`}
    >
      {children}
    </div>
  );
}
