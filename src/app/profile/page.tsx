"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { getSupabaseBrowser } from "@/lib/supabase";

function initialsFromName(name: string, email: string): string {
  const source = (name || email || "").trim();
  if (!source) return "U";
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default function ProfilePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const supabase = getSupabaseBrowser();
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (!user) {
        router.push("/login?next=/profile");
        return;
      }
      setEmail(user.email ?? "");
      setFullName(
        (user.user_metadata?.full_name as string | undefined) ||
          (user.user_metadata?.name as string | undefined) ||
          ""
      );
      setLoading(false);
    });
  }, [router]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const supabase = getSupabaseBrowser();
      const { error: updateError } = await supabase.auth.updateUser({
        data: {
          full_name: fullName.trim(),
        },
      });
      if (updateError) {
        setError(updateError.message);
      } else {
        setMessage("Profile updated.");
        router.refresh();
      }
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-12">
        <p className="text-sm text-muted">Loading profile settings...</p>
      </div>
    );
  }

  const displayName = fullName.trim() || "Unnamed User";
  const initials = initialsFromName(fullName, email);

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <section className="mb-6 rounded-3xl border border-border bg-white p-6 shadow-[var(--card-shadow)]">
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-foreground text-lg font-semibold text-white">
            {initials}
          </div>
          <div className="min-w-0">
            <p className="truncate text-lg font-semibold">{displayName}</p>
            <p className="truncate text-sm text-muted">{email}</p>
          </div>
        </div>
      </section>

      {error ? (
        <div className="mb-4 rounded-2xl border border-red-100 bg-red-50 p-3 text-sm text-warning">
          {error}
        </div>
      ) : null}
      {message ? (
        <div className="mb-4 rounded-2xl border border-green-100 bg-green-50 p-3 text-sm text-success">
          {message}
        </div>
      ) : null}

      <Card className="rounded-3xl border border-border p-7">
        <form onSubmit={handleSave} className="space-y-5">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
            <p className="mt-1 text-sm text-muted">Update your name used across AiraHost.</p>
          </div>

          <div className="space-y-2">
            <label className="block text-sm font-medium text-foreground">Email</label>
            <input
              value={email}
              disabled
              className="input w-full cursor-not-allowed bg-gray-50 text-muted"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-sm font-medium text-foreground">Display name</label>
            <input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Enter your name"
              maxLength={80}
              className="input w-full"
            />
          </div>

          <div className="pt-1">
            <Button type="submit" disabled={saving}>
              {saving ? "Saving..." : "Save changes"}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
