'use client'

import { useState, useEffect } from 'react'
import { useAuth, SignOutButton } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { HistoryItem } from '@/lib/api'

async function resolveToken(getToken: () => Promise<string | null>): Promise<string | null> {
  const token = await getToken()
  if (token) return token
  await new Promise(r => setTimeout(r, 350))
  return getToken()
}

function formatDate(d: string) {
  try {
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '' }
}

function SentimentBadge({ sentiment }: { sentiment: HistoryItem['overall_sentiment'] }) {
  if (!sentiment) return null
  const styles = {
    Positive: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20',
    Mixed:    'text-slate-300 bg-slate-300/10 border-slate-300/20',
    Negative: 'text-rose-400 bg-rose-400/10 border-rose-400/20',
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${styles[sentiment]}`}>
      {sentiment}
    </span>
  )
}

function Background() {
  return (
    <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden="true">
      <div className="blob-1 absolute -top-40 -left-40 h-[700px] w-[700px] rounded-full bg-violet-600/20 blur-3xl" />
      <div className="blob-2 absolute top-1/3 -right-20 h-[500px] w-[500px] rounded-full bg-indigo-500/15 blur-3xl" />
      <div className="blob-3 absolute -bottom-20 left-1/3 h-[400px] w-[400px] rounded-full bg-cyan-600/10 blur-3xl" />
      <div className="grain absolute inset-0" />
    </div>
  )
}

const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
}

const item = {
  hidden:  { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.45, ease: 'easeOut' as const } },
}

export default function HistoryPage() {
  const { isLoaded, isSignedIn, getToken } = useAuth()
  const router = useRouter()

  const [reports, setReports] = useState<HistoryItem[]>([])
  const [pageState, setPageState] = useState<'loading' | 'loaded' | 'error'>('loading')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)

  useEffect(() => {
    if (!isLoaded) return
    if (!isSignedIn) { router.push('/'); return }
    let cancelled = false
    ;(async () => {
      const token = await resolveToken(getToken)
      if (!token || cancelled) return
      try {
        const data = await api.getHistory(token)
        if (!cancelled) { setReports(data.reports); setPageState('loaded') }
      } catch {
        if (!cancelled) setPageState('error')
      }
    })()
    return () => { cancelled = true }
  }, [isLoaded, isSignedIn]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDelete = async (reportId: string) => {
    setDeletingId(reportId)
    try {
      const token = await resolveToken(getToken)
      if (!token) return
      await api.deleteReport(token, reportId)
      setReports(prev => prev.filter(r => r.report_id !== reportId))
    } catch {
      // silently ignore — item stays in list
    } finally {
      setDeletingId(null)
    }
  }

  const handleClearAll = async () => {
    if (!window.confirm('Clear all history? This cannot be undone.')) return
    setClearing(true)
    try {
      const token = await resolveToken(getToken)
      if (!token) return
      await api.clearHistory(token)
      setReports([])
    } catch {
      // silently ignore
    } finally {
      setClearing(false)
    }
  }

  if (!isLoaded || pageState === 'loading') {
    return (
      <div className="relative min-h-screen flex items-center justify-center">
        <Background />
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" />
      </div>
    )
  }

  if (pageState === 'error') {
    return (
      <div className="relative min-h-screen flex flex-col items-center justify-center gap-4">
        <Background />
        <p className="text-sm text-white/50">Failed to load history.</p>
        <button
          onClick={() => router.push('/')}
          className="text-sm text-white/35 hover:text-white/60 transition-colors"
        >
          ← Back to home
        </button>
      </div>
    )
  }

  return (
    <div className="relative min-h-screen">
      <Background />

      {/* Nav */}
      <header className="sticky top-0 z-20 flex items-center justify-between px-6 py-3.5">
        <button
          onClick={() => router.push('/')}
          className="flex items-center gap-2 text-sm font-medium text-white/65 hover:text-white/90 transition-colors"
        >
          <Search className="h-3.5 w-3.5" />
          Lens
        </button>
        <SignOutButton>
          <Button variant="ghost" size="sm" className="text-white/30 hover:text-white/60 hover:bg-white/5">
            Sign out
          </Button>
        </SignOutButton>
      </header>

      {/* Content */}
      <main className="relative z-10 mx-auto max-w-3xl px-6 py-10">
        <motion.div className="space-y-6" variants={stagger} initial="hidden" animate="visible">

          {/* Header row */}
          <motion.div variants={item} className="flex items-center justify-between">
            <h1 className="text-2xl font-bold text-white/90">History</h1>
            {reports.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                disabled={clearing}
                onClick={handleClearAll}
                className="text-white/30 hover:text-rose-400/70 hover:bg-rose-400/5 disabled:opacity-40"
              >
                Clear all
              </Button>
            )}
          </motion.div>

          {/* Empty state */}
          {reports.length === 0 && (
            <motion.div variants={item} className="flex flex-col items-center justify-center py-24 gap-4">
              <p className="text-sm text-white/35">No research yet.</p>
              <button
                onClick={() => router.push('/')}
                className="text-sm text-violet-400/70 hover:text-violet-400 transition-colors"
              >
                Start your first query →
              </button>
            </motion.div>
          )}

          {/* Report list */}
          <AnimatePresence initial={false}>
            {reports.map(r => (
              <motion.div
                key={r.report_id}
                variants={item}
                initial="hidden"
                animate="visible"
                exit={{ opacity: 0, y: -8, transition: { duration: 0.2 } }}
                className="group flex items-center justify-between gap-4 rounded-2xl border border-white/[0.07] bg-white/[0.03] backdrop-blur-sm px-5 py-4 hover:bg-white/[0.05] transition-colors cursor-pointer"
                onClick={() => router.push(`/chat/${r.report_id}`)}
              >
                <div className="min-w-0 space-y-1.5">
                  <p className="text-sm font-medium text-white/85 leading-snug truncate">{r.query}</p>
                  <div className="flex items-center gap-2.5">
                    <SentimentBadge sentiment={r.overall_sentiment} />
                    <span className="text-xs text-white/30">{formatDate(r.created_at)}</span>
                  </div>
                </div>
                <button
                  onClick={e => { e.stopPropagation(); handleDelete(r.report_id) }}
                  disabled={deletingId === r.report_id}
                  className="shrink-0 h-7 w-7 flex items-center justify-center rounded-md text-white/20 hover:text-rose-400/70 hover:bg-rose-400/5 opacity-0 group-hover:opacity-100 transition-all disabled:opacity-40"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </motion.div>
            ))}
          </AnimatePresence>

        </motion.div>
      </main>
    </div>
  )
}
