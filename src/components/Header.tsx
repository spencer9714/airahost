import Link from "next/link";

export function Header() {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          AiraHost
        </Link>
        <nav className="flex items-center gap-6 text-sm text-muted">
          <Link href="/tool" className="transition-colors hover:text-foreground">
            Analyze
          </Link>
          <Link
            href="/dashboard"
            className="transition-colors hover:text-foreground"
          >
            Dashboard
          </Link>
        </nav>
      </div>
    </header>
  );
}
