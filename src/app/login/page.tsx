"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { getSupabaseBrowser } from "@/lib/supabase";

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-md px-6 py-16 text-sm text-muted">Loading...</div>}>
      <LoginContent />
    </Suspense>
  );
}

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/dashboard";
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setMessage("");

    const supabase = getSupabaseBrowser();

    if (mode === "signup") {
      if (!fullName.trim()) {
        setError("Please enter your name.");
        setLoading(false);
        return;
      }

      const { error: signUpError } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
          data: {
            full_name: fullName.trim(),
          },
        },
      });
      if (signUpError) {
        setError(signUpError.message);
      } else {
        setMessage("Check your email for a confirmation link.");
      }
    } else {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (signInError) {
        setError(signInError.message);
      } else {
        router.push(next);
        router.refresh();
      }
    }
    setLoading(false);
  }

  return (
    <div className="mx-auto max-w-md px-6 py-16">
      <h1 className="mb-2 text-center text-3xl font-bold">
        {mode === "signin" ? "Welcome back" : "Create account"}
      </h1>
      <p className="mb-8 text-center text-muted">
        {mode === "signin"
          ? "Sign in to manage your listings and reports."
          : "Create an account to save listings and track pricing."}
      </p>
      {searchParams.get("error") === "auth" ? (
        <p className="mb-4 rounded-xl bg-red-50 p-3 text-sm text-warning">
          Authentication failed. Please try signing in again.
        </p>
      ) : null}

      <Card>
        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "signup" ? (
            <div>
              <label className="mb-1.5 block text-sm font-medium">Name</label>
              <input
                type="text"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Your name"
                required={mode === "signup"}
                className="input w-full"
              />
            </div>
          ) : null}

          <div>
            <label className="mb-1.5 block text-sm font-medium">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
              className="input w-full"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 6 characters"
              required
              minLength={6}
              className="input w-full"
            />
          </div>

          {error && (
            <p className="rounded-xl bg-red-50 p-3 text-sm text-warning">
              {error}
            </p>
          )}

          {message && (
            <p className="rounded-xl bg-green-50 p-3 text-sm text-success">
              {message}
            </p>
          )}

          <Button type="submit" disabled={loading} className="w-full">
            {loading
              ? "Loading..."
              : mode === "signin"
                ? "Sign in"
                : "Create account"}
          </Button>
        </form>

        <div className="mt-4 text-center text-sm text-muted">
          {mode === "signin" ? (
            <p>
              Don&apos;t have an account?{" "}
              <button
                onClick={() => {
                  setMode("signup");
                  setError("");
                  setMessage("");
                }}
                className="font-medium text-accent hover:underline"
              >
                Sign up
              </button>
            </p>
          ) : (
            <p>
              Already have an account?{" "}
              <button
                onClick={() => {
                  setMode("signin");
                  setError("");
                  setMessage("");
                  setFullName("");
                }}
                className="font-medium text-accent hover:underline"
              >
                Sign in
              </button>
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
