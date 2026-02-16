import Link from "next/link";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { UserMenu } from "./UserMenu";

export async function Header() {
  let user = null;
  let displayName = "";
  try {
    const supabase = await getSupabaseServer();
    const {
      data: { user: authUser },
    } = await supabase.auth.getUser();
    user = authUser;
    displayName =
      (authUser?.user_metadata?.full_name as string | undefined) ||
      (authUser?.user_metadata?.name as string | undefined) ||
      "";
  } catch {
    // Not authenticated or server error
  }

  return (
    <header className="border-b border-border">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          AiraHost
        </Link>
        <nav className="flex items-center gap-6 text-sm text-muted">
          <Link
            href="/tool"
            className="transition-colors hover:text-foreground"
          >
            Analyze
          </Link>
          <Link
            href="/dashboard"
            className="transition-colors hover:text-foreground"
          >
            Dashboard
          </Link>
          {user ? (
            <UserMenu
              email={user.email ?? ""}
              displayName={displayName}
            />
          ) : (
            <Link
              href="/login"
              className="rounded-lg bg-accent px-4 py-1.5 text-white transition-colors hover:bg-accent-hover"
            >
              Sign in
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}

