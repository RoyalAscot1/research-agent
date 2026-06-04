'use client'

import { motion, AnimatePresence } from "framer-motion";
import { useAuth, SignInButton, SignOutButton } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

type Status = "idle" | "submitting" | "polling" | "error";

async function resolveToken(getToken: () => Promise<string | null>): Promise<string | null> {
  const token = await getToken();
  if (token) return token;
  await new Promise((r) => setTimeout(r, 350));
  return getToken();
}

const PLACEHOLDERS = [
  "Impact of AI on the job market",
  "Electric vehicle adoption trends",
  "Climate tech public sentiment",
  "Future of remote work",
  "Space exploration investment outlook",
];

function useTypewriter(phrases: string[]) {
  const [text, setText] = useState("");
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const phrase = phrases[phraseIdx];
    if (!deleting && text === phrase) {
      const t = setTimeout(() => setDeleting(true), 2400);
      return () => clearTimeout(t);
    }
    if (deleting && text === "") {
      setDeleting(false);
      setPhraseIdx((i) => (i + 1) % phrases.length);
      return;
    }
    const t = setTimeout(
      () => setText(deleting ? phrase.slice(0, text.length - 1) : phrase.slice(0, text.length + 1)),
      deleting ? 22 : 58,
    );
    return () => clearTimeout(t);
  }, [text, deleting, phraseIdx, phrases]);

  return text;
}

const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.11 } },
  exit:    { transition: { staggerChildren: 0.04, staggerDirection: -1 } },
};

const item = {
  hidden:  { opacity: 0, y: 22 },
  visible: { opacity: 1, y: 0,   transition: { duration: 0.65, ease: "easeOut" as const } },
  exit:    { opacity: 0, y: -10, transition: { duration: 0.22 } },
};

function Background() {
  return (
    <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden="true">
      <div className="blob-1 absolute -top-40 -left-40 h-[700px] w-[700px] rounded-full bg-violet-600/20 blur-3xl" />
      <div className="blob-2 absolute top-1/3 -right-20 h-[500px] w-[500px] rounded-full bg-indigo-500/15 blur-3xl" />
      <div className="blob-3 absolute -bottom-20 left-1/3 h-[400px] w-[400px] rounded-full bg-cyan-600/10 blur-3xl" />
      <div className="grain absolute inset-0" />
    </div>
  );
}

const Wordmark = () => (
  <h1 className="text-8xl font-bold tracking-tight bg-gradient-to-br from-white via-violet-200 to-cyan-300 bg-clip-text text-transparent leading-none pb-2 select-none">
    Lens
  </h1>
);

const Tagline = () => (
  <p className="text-base text-white/35 tracking-widest uppercase">
    AI research &amp; sentiment analysis
  </p>
);

export default function HomePage() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const router = useRouter();

  const [query, setQuery]   = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [jobId, setJobId]   = useState<string | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  const cancelledRef = useRef(false);
  const placeholder = useTypewriter(PLACEHOLDERS);

  useEffect(() => {
    if (status !== "polling" || !jobId) return;
    cancelledRef.current = false;

    const poll = async () => {
      if (cancelledRef.current) return;
      try {
        const token = await resolveToken(getToken);
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
        if (!cancelledRef.current) { setStatus("error"); setError("Something went wrong. Please try again."); }
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
      const token = await resolveToken(getToken);
      if (!token) { setStatus("error"); setError("Session expired. Please sign in again."); return; }
      let result: { job_id: string; status: string } | null = null;
      try {
        result = await api.createQuery(token, query.trim());
      } catch (firstErr) {
        console.warn("createQuery first attempt failed, retrying in 500ms:", firstErr);
        await new Promise((r) => setTimeout(r, 500));
        const retryToken = await resolveToken(getToken);
        if (!retryToken) { setStatus("error"); setError("Session expired. Please sign in again."); return; }
        result = await api.createQuery(retryToken, query.trim());
      }
      setJobId(result.job_id);
      setStatus("polling");
    } catch (err) {
      console.error("createQuery failed:", err);
      setStatus("error");
      setError("Failed to start research. Please try again.");
    }
  };

  const handleReset = () => { setStatus("idle"); setError(null); setJobId(null); };

  const isPolling = status === "polling" || status === "submitting";

  return (
    <div className="relative min-h-screen flex flex-col">
      <Background />

      {isLoaded && isSignedIn && (
        <motion.div
          className="absolute top-5 right-5 z-10 flex items-center gap-1"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.9 }}
        >
          <Button variant="ghost" size="sm" onClick={() => router.push('/history')} className="text-white/30 hover:text-white/60 hover:bg-white/5">
            History
          </Button>
          <SignOutButton>
            <Button variant="ghost" size="sm" className="text-white/30 hover:text-white/60 hover:bg-white/5">
              Sign out
            </Button>
          </SignOutButton>
        </motion.div>
      )}

      <main className="flex flex-1 flex-col items-center justify-center p-8">
        <AnimatePresence mode="wait">

          {/* Loading */}
          {!isLoaded && (
            <motion.div key="loading" variants={item} initial="hidden" animate="visible" exit="exit">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" />
            </motion.div>
          )}

          {/* Sign in */}
          {isLoaded && !isSignedIn && (
            <motion.div key="signin" className="flex flex-col items-center gap-10" variants={stagger} initial="hidden" animate="visible" exit="exit">
              <motion.div className="space-y-4 text-center" variants={item}>
                <Wordmark />
                <Tagline />
              </motion.div>
              <motion.div variants={item}>
                <SignInButton>
                  <motion.div
                    whileHover={{ scale: 1.04 }}
                    whileTap={{ scale: 0.96 }}
                    transition={{ type: "spring", stiffness: 380, damping: 18 }}
                  >
                    <Button className="bg-violet-600 hover:bg-violet-500 text-white border-0 px-8 h-11 text-base font-medium">
                      Sign in to get started
                    </Button>
                  </motion.div>
                </SignInButton>
              </motion.div>
            </motion.div>
          )}

          {/* Researching */}
          {isLoaded && isSignedIn && isPolling && (
            <motion.div key="polling" className="flex flex-col items-center gap-6" variants={stagger} initial="hidden" animate="visible" exit="exit">
              <motion.div variants={item} className="relative flex items-center justify-center">
                <div className="h-12 w-12 rounded-full border border-violet-500/15 bg-violet-500/5" />
                <div className="absolute h-12 w-12 animate-spin rounded-full border-2 border-transparent border-t-violet-400" />
              </motion.div>
              <motion.div variants={item} className="space-y-2 text-center">
                <p className="text-sm font-medium text-white/55">
                  {status === "submitting" ? "Starting research…" : "Researching…"}
                </p>
                <p className="max-w-xs text-xs text-white/22 leading-relaxed">{query}</p>
              </motion.div>
            </motion.div>
          )}

          {/* Prompt */}
          {isLoaded && isSignedIn && !isPolling && (
            <motion.div key="prompt" className="w-full max-w-2xl space-y-10" variants={stagger} initial="hidden" animate="visible" exit="exit">

              <motion.div className="space-y-4 text-center" variants={item}>
                <Wordmark />
                <Tagline />
              </motion.div>

              <motion.div variants={item}>
                <motion.div
                  className="rounded-2xl border p-px backdrop-blur-xl shadow-2xl shadow-black/50"
                  animate={{
                    borderColor: focused
                      ? "rgba(139, 92, 246, 0.4)"
                      : "rgba(255, 255, 255, 0.07)",
                    boxShadow: focused
                      ? "0 0 0 3px rgba(139, 92, 246, 0.08), 0 25px 60px rgba(0,0,0,0.5), 0 0 100px rgba(139, 92, 246, 0.07)"
                      : "0 25px 60px rgba(0,0,0,0.5)",
                  }}
                  transition={{ duration: 0.3 }}
                >
                  <div className="rounded-[15px] bg-white/[0.035] overflow-hidden">
                    <div className="border-b border-white/[0.05]" />
                    <form onSubmit={handleSubmit} className="px-7 pt-6 pb-5 space-y-5">
                      <textarea
                        className="w-full resize-none bg-transparent text-base text-white/85 placeholder:text-white/18 focus:outline-none disabled:opacity-40 leading-relaxed"
                        rows={5}
                        placeholder={!query && !focused ? `${placeholder}▌` : "What do you want to research?"}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onFocus={() => setFocused(true)}
                        onBlur={() => setFocused(false)}
                        disabled={status !== "idle"}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            handleSubmit(e as unknown as React.FormEvent);
                          }
                        }}
                      />

                      <AnimatePresence>
                        {error && (
                          <motion.p
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="text-xs text-red-400/80"
                          >
                            {error}
                          </motion.p>
                        )}
                      </AnimatePresence>

                      <div className="flex items-center justify-between border-t border-white/[0.05] pt-4">
                        <span className="text-xs text-white/14 select-none">
                          ↵ research · ⇧↵ newline
                        </span>
                        <div className="flex gap-2">
                          <AnimatePresence>
                            {status === "error" && (
                              <motion.div initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 8 }}>
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={handleReset}
                                  className="border-white/10 text-white/40 hover:bg-white/5 hover:text-white/70 h-9 px-4 text-sm"
                                >
                                  Reset
                                </Button>
                              </motion.div>
                            )}
                          </AnimatePresence>
                          <motion.div
                            whileHover={{ scale: 1.04 }}
                            whileTap={{ scale: 0.95 }}
                            transition={{ type: "spring", stiffness: 400, damping: 18 }}
                          >
                            <Button
                              type="submit"
                              disabled={!query.trim() || status !== "idle"}
                              className="bg-violet-600 hover:bg-violet-500 text-white border-0 disabled:opacity-30 h-9 px-5 text-sm font-medium"
                            >
                              Research
                            </Button>
                          </motion.div>
                        </div>
                      </div>
                    </form>
                  </div>
                </motion.div>
              </motion.div>

            </motion.div>
          )}

        </AnimatePresence>
      </main>
    </div>
  );
}
