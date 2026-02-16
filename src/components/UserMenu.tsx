"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabaseBrowser } from "@/lib/supabase";

function initialsFromName(name: string, email: string): string {
  const source = (name || email || "").trim();
  if (!source) return "U";
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export function UserMenu({
  email,
  displayName,
}: {
  email: string;
  displayName?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  async function handleSignOut() {
    const supabase = getSupabaseBrowser();
    await supabase.auth.signOut();
    setOpen(false);
    router.push("/");
    router.refresh();
  }

  const showName = displayName || email;
  const initials = initialsFromName(displayName || "", email);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full border border-border bg-white px-2 py-1.5 shadow-sm transition-colors hover:border-foreground/30"
        aria-label="Open user menu"
      >
        <span className="text-sm text-foreground">☰</span>
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-200 text-xs font-semibold text-foreground">
          {initials}
        </span>
      </button>

      {open ? (
        <div className="absolute right-0 z-50 mt-2 w-64 rounded-2xl border border-border bg-white p-2 shadow-lg">
          <div className="rounded-xl px-3 py-2">
            <p className="truncate text-sm font-medium text-foreground">{showName}</p>
            <p className="truncate text-xs text-muted">{email}</p>
          </div>
          <div className="my-1 border-t border-border" />
          <Link
            href="/dashboard"
            onClick={() => setOpen(false)}
            className="block rounded-xl px-3 py-2 text-sm text-foreground transition-colors hover:bg-gray-50"
          >
            Dashboard
          </Link>
          <Link
            href="/profile"
            onClick={() => setOpen(false)}
            className="block rounded-xl px-3 py-2 text-sm text-foreground transition-colors hover:bg-gray-50"
          >
            Profile
          </Link>
          <button
            type="button"
            onClick={handleSignOut}
            className="mt-1 block w-full rounded-xl px-3 py-2 text-left text-sm text-foreground transition-colors hover:bg-gray-50"
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
