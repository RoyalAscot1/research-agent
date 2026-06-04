'use client'

import { use, useState, useEffect } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { Check, Copy, Download, Search } from 'lucide-react'
import { api } from '@/lib/api'
import type { ReportData } from '@/lib/api'

async function resolveToken(getToken: () => Promise<string | null>): Promise<string | null> {
  const token = await getToken()
  if (token) return token
  await new Promise(r => setTimeout(r, 350))
  return getToken()
}

function formatDuration(s: number) {
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

function extractDomain(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, '') }
  catch { return '' }
}

function formatPubDate(d: string | null | undefined) {
  if (!d) return ''
  try {
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch { return '' }
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

function SentimentTile({ label, value, color }: { label: string; value: number | null; color: string }) {
  return (
    <div className="flex-1 rounded-xl border border-white/[0.07] bg-white/[0.04] px-5 py-4">
      <p className="text-xs text-white/40 mb-2 tracking-wide">{label}</p>
      <p className={`text-4xl font-bold tabular-nums ${color}`}>
        {value != null ? `${Math.round(value * 100)}%` : '—'}
      </p>
    </div>
  )
}

function SourceCard({
  title, url, publishedDate,
}: {
  title: string
  url: string
  publishedDate?: string | null
}) {
  const domain = extractDomain(url)
  const date = formatPubDate(publishedDate)

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded-xl border border-white/[0.07] bg-white/[0.025] px-5 py-4 hover:bg-white/[0.05] transition-colors"
    >
      <p className="text-sm font-medium text-white/85 leading-snug mb-1.5">{title}</p>
      <p className="text-xs text-white/35">
        {domain}{date ? ` · ${date}` : ''}
      </p>
    </a>
  )
}

const mdComponents: Partial<Components> = {
  h1({ children }) {
    return <h1 className="text-xl font-bold text-white/90 mt-6 mb-3 first:mt-0">{children}</h1>
  },
  h2({ children }) {
    return <h2 className="text-base font-semibold text-white/80 mt-5 mb-2">{children}</h2>
  },
  h3({ children }) {
    return <h3 className="text-sm font-semibold text-white/75 mt-4 mb-1.5">{children}</h3>
  },
  p({ children }) {
    return <p className="text-sm text-white/65 leading-relaxed mb-4 last:mb-0">{children}</p>
  },
  ul({ children }) {
    return <ul className="list-disc list-outside pl-4 text-sm text-white/65 mb-4 space-y-1">{children}</ul>
  },
  ol({ children }) {
    return <ol className="list-decimal list-outside pl-4 text-sm text-white/65 mb-4 space-y-1">{children}</ol>
  },
  li({ children }) {
    return <li className="leading-relaxed">{children}</li>
  },
  strong({ children }) {
    return <strong className="text-white/88 font-semibold">{children}</strong>
  },
  em({ children }) {
    return <em className="text-white/55 italic">{children}</em>
  },
  hr() {
    return <hr className="border-white/[0.08] my-5" />
  },
  blockquote({ children }) {
    return <blockquote className="border-l-2 border-violet-500/30 pl-4 text-white/50 italic my-4">{children}</blockquote>
  },
  pre({ children }) {
    return <pre className="bg-white/[0.04] rounded-lg p-4 my-4 overflow-x-auto text-xs font-mono text-white/70">{children}</pre>
  },
  code({ children, className }) {
    if (className) return <code className={className}>{children}</code>
    return <code className="bg-white/[0.06] text-violet-300 px-1.5 py-0.5 rounded text-xs font-mono">{children}</code>
  },
}

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { isLoaded, isSignedIn, getToken } = useAuth()
  const router = useRouter()

  const [report, setReport] = useState<ReportData | null>(null)
  const [pageState, setPageState] = useState<'loading' | 'loaded' | 'error'>('loading')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!isLoaded) return
    if (!isSignedIn) { router.push('/'); return }
    let cancelled = false
    ;(async () => {
      const token = await resolveToken(getToken)
      if (!token || cancelled) return
      try {
        const data = await api.getReport(token, id)
        if (!cancelled) { setReport(data); setPageState('loaded') }
      } catch {
        if (!cancelled) setPageState('error')
      }
    })()
    return () => { cancelled = true }
  }, [isLoaded, isSignedIn]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCopy = async () => {
    if (!report?.report_markdown) return
    await navigator.clipboard.writeText(report.report_markdown)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = () => {
    if (!report?.report_markdown) return
    const blob = new Blob([report.report_markdown], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `lens-report.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!isLoaded || pageState === 'loading') {
    return (
      <div className="relative min-h-screen flex items-center justify-center">
        <Background />
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" />
      </div>
    )
  }

  if (pageState === 'error' || !report) {
    return (
      <div className="relative min-h-screen flex flex-col items-center justify-center gap-4">
        <Background />
        <p className="text-sm text-white/50">Failed to load report.</p>
        <button
          onClick={() => router.push('/')}
          className="text-sm text-white/35 hover:text-white/60 transition-colors"
        >
          ← Back to home
        </button>
      </div>
    )
  }

  const hasSentiment = report.sentiment_positive != null

  return (
    <div className="relative min-h-screen">
      <Background />

      {/* Nav */}
      <header className="sticky top-0 z-20 flex items-center justify-between px-6 py-3.5">
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.push('/')}
            className="flex items-center gap-2 text-sm font-medium text-white/65 hover:text-white/90 transition-colors"
          >
            <Search className="h-3.5 w-3.5" />
            Lens
          </button>
          <button
            onClick={() => router.push('/history')}
            className="text-sm text-white/40 hover:text-white/70 transition-colors"
          >
            History
          </button>
        </div>
        <div className="flex items-center gap-1">
          {report.completed_in_seconds != null && (
            <span className="mr-2 text-xs text-white/30 select-none">
              Completed · {formatDuration(report.completed_in_seconds)}
            </span>
          )}
          <button
            onClick={handleCopy}
            className="h-7 w-7 flex items-center justify-center rounded-md text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors"
            title="Copy markdown"
          >
            {copied
              ? <Check className="h-3.5 w-3.5 text-emerald-400" />
              : <Copy className="h-3.5 w-3.5" />
            }
          </button>
          <button
            onClick={handleDownload}
            className="h-7 w-7 flex items-center justify-center rounded-md text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors"
            title="Download markdown"
          >
            <Download className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Content */}
      <main className="relative z-10 mx-auto max-w-3xl px-6 py-10">
        <motion.div className="space-y-5" variants={stagger} initial="hidden" animate="visible">

          {/* Query title */}
          <motion.h1 variants={item} className="text-2xl font-bold text-white/90 leading-snug">
            {report.query ?? 'Research report'}
          </motion.h1>

          {/* Sentiment tiles */}
          {hasSentiment && (
            <motion.div variants={item} className="flex gap-3">
              <SentimentTile label="Positive" value={report.sentiment_positive} color="text-emerald-400" />
              <SentimentTile label="Neutral"  value={report.sentiment_neutral}  color="text-slate-300" />
              <SentimentTile label="Negative" value={report.sentiment_negative} color="text-rose-400" />
            </motion.div>
          )}

          {/* Report markdown */}
          <motion.div
            variants={item}
            className="rounded-2xl border border-white/[0.07] bg-white/[0.03] backdrop-blur-sm px-7 py-6"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {report.report_markdown ?? ''}
            </ReactMarkdown>
          </motion.div>

          {/* Sources */}
          {report.sources.length > 0 && (
            <motion.div variants={item} className="space-y-3 pt-1">
              <p className="text-xs text-white/35 tracking-wide uppercase">
                Sources · {report.sources.length} {report.sources.length === 1 ? 'reference' : 'references'}
              </p>
              <div className="space-y-2">
                {report.sources.map((s, i) => (
                  <SourceCard
                    key={i}
                    title={s.title}
                    url={s.url}
                    publishedDate={s.published_date}
                  />
                ))}
              </div>
            </motion.div>
          )}

          {/* Suggested follow-ups */}
          {report.suggested_followups && report.suggested_followups.length > 0 && (
            <motion.div variants={item} className="space-y-3 pt-1">
              <p className="text-xs text-white/35 tracking-wide uppercase">Suggested follow-ups</p>
              <div className="flex flex-wrap gap-2">
                {report.suggested_followups.map((q, i) => (
                  <button
                    key={i}
                    className="rounded-full border border-white/[0.08] bg-white/[0.04] px-4 py-1.5 text-xs text-white/50 hover:text-white/75 hover:bg-white/[0.07] transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </motion.div>
          )}

          <div className="h-8" />
        </motion.div>
      </main>
    </div>
  )
}
