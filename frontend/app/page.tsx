'use client'

import { useAuth, SignInButton, SignOutButton } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

type Status = "idle" | "submitting" | "polling" | "error";

export default function HomePage() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const router = useRouter();

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (status !== "polling" || !jobId) return;

    cancelledRef.current = false;

    const poll = async () => {
      if (cancelledRef.current) return;
      try {
        const token = await getToken();
        if (!token) { setStatus("error"); setError("Session expired. Please sign in again."); return; }
        const data = await api.getJobStatus(token, jobId);
        if (data.status === "done" && data.report_id) {
          router.push(`/chat/${data.report_id}`);
        } else if (data.status === "failed") {
          setStatus("error");
          setError("Research failed. Please try again.");
        } else {
          setTimeout(poll, 2000);
        }
      } catch {
        if (!cancelledRef.current) {
          setStatus("error");
          setError("Something went wrong. Please try again.");
        }
      }
    };

    poll();

    return () => { cancelledRef.current = true; };
  }, [status, jobId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setStatus("submitting");
    setError(null);
    try {
      const token = await getToken();
      if (!token) { setStatus("error"); setError("Session expired. Please sign in again."); return; }
      const { job_id } = await api.createQuery(token, query.trim());
      setJobId(job_id);
      setStatus("polling");
    } catch {
      setStatus("error");
      setError("Failed to start research. Please try again.");
    }
  };

  const handleReset = () => {
    setStatus("idle");
    setError(null);
    setJobId(null);
  };

  if (!isLoaded) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center p-8">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
      </main>
    );
  }

  if (!isSignedIn) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
        <h1 className="text-3xl font-semibold tracking-tight">Lens</h1>
        <p className="text-sm text-muted-foreground">AI research and sentiment analysis</p>
        <SignInButton>
          <Button>Sign in to get started</Button>
        </SignInButton>
      </main>
    );
  }

  if (status === "polling" || status === "submitting") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
        <p className="text-sm text-muted-foreground">
          {status === "submitting" ? "Starting research…" : "Researching…"}
        </p>
        <p className="max-w-sm text-center text-xs text-muted-foreground/60">{query}</p>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="absolute top-4 right-4">
        <SignOutButton>
          <Button variant="ghost" size="sm">Sign out</Button>
        </SignOutButton>
      </div>
      <div className="w-full max-w-xl space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-3xl font-semibold tracking-tight">Lens</h1>
          <p className="text-sm text-muted-foreground">AI research and sentiment analysis</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <textarea
            className="w-full resize-none rounded-lg border border-border bg-background px-4 py-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/50 disabled:opacity-50"
            rows={3}
            placeholder="What do you want to research?"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={status !== "idle"}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(e as unknown as React.FormEvent); }
            }}
          />
          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
          <div className="flex justify-end gap-2">
            {status === "error" && (
              <Button type="button" variant="outline" onClick={handleReset}>
                Reset
              </Button>
            )}
            <Button type="submit" disabled={!query.trim() || status !== "idle"}>
              Research
            </Button>
          </div>
        </form>
      </div>
    </main>
  );
}
