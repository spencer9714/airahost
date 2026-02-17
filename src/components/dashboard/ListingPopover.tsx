"use client";

import { useEffect, useRef, useState } from "react";

interface ListingOption {
  id: string;
  name: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  listings: ListingOption[];
  selectedId: string;
  onSelect: (id: string) => void;
}

export function ListingPopover({
  open,
  onClose,
  listings,
  selectedId,
  onSelect,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");

  // Reset search when opening
  useEffect(() => {
    if (open) {
      setQuery("");
      // Small delay to let the DOM render before focusing
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open, onClose]);

  if (!open) return null;

  const filtered = listings.filter((l) =>
    l.name.toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-2 w-72 rounded-xl border border-border bg-white p-2 shadow-lg"
    >
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search listings..."
        className="mb-2 w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-accent"
      />
      <div className="max-h-48 overflow-y-auto">
        {filtered.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted">No listings found</p>
        ) : (
          filtered.map((l) => (
            <button
              key={l.id}
              type="button"
              onClick={() => {
                onSelect(l.id);
                onClose();
              }}
              className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                selectedId === l.id
                  ? "bg-gray-100 font-medium text-foreground"
                  : "text-muted hover:bg-gray-50 hover:text-foreground"
              }`}
            >
              {l.name}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
