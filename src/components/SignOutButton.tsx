"use client";

import { useRouter } from "next/navigation";
import { getSupabaseBrowser } from "@/lib/supabase";

export function SignOutButton({
  email,
  displayName,
}: {
  email: string;
  displayName?: string;
}) {
  const router = useRouter();

  async function handleSignOut() {
    const supabase = getSupabaseBrowser();
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-3">
      <span
        className="max-w-[160px] truncate text-xs text-muted"
        title={displayName || email}
      >
        {displayName || email}
      </span>
      <button
        onClick={handleSignOut}
        className="rounded-lg border border-border px-3 py-1.5 text-xs transition-colors hover:border-foreground/30 hover:text-foreground"
      >
        Sign out
      </button>
    </div>
  );
}
