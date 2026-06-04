export interface HistoryItem {
  report_id: string;
  job_id: string;
  query: string;
  overall_sentiment: "Positive" | "Mixed" | "Negative" | null;
  created_at: string;
}

export interface ReportSource {
  title: string;
  url: string;
  published_date: string | null;
  score: number | null;
}

export interface ReportData {
  report_id: string;
  job_id: string;
  query: string | null;
  report_markdown: string | null;
  sentiment_positive: number | null;
  sentiment_neutral: number | null;
  sentiment_negative: number | null;
  youtube_comment_volume: number | null;
  overall_sentiment: "Positive" | "Mixed" | "Negative" | null;
  source_count: number | null;
  sources: ReportSource[];
  suggested_followups: string[] | null;
  created_at: string;
  completed_in_seconds: number | null;
  follow_ups: Array<{
    question: string;
    answer: string | null;
    turn_number: number;
  }>;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
      ...init?.headers,
    },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  createQuery: (token: string, query: string) =>
    apiFetch<{ job_id: string; status: string }>("/queries", token, {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  getJobStatus: (token: string, jobId: string) =>
    apiFetch<{ job_id: string; status: string; completed_at: string | null; report_id?: string }>(
      `/jobs/${jobId}/status`,
      token,
    ),

  getReport: (token: string, reportId: string) =>
    apiFetch<ReportData>(`/reports/${reportId}`, token),

  createFollowup: (token: string, reportId: string, question: string) =>
    apiFetch<{ answer: string; turn_number: number }>(
      `/reports/${reportId}/followup`,
      token,
      { method: "POST", body: JSON.stringify({ question }) }
    ),

  getHistory: (token: string) =>
    apiFetch<{ reports: HistoryItem[] }>("/history", token),

  deleteReport: (token: string, reportId: string) =>
    apiFetch<void>(`/history/${reportId}`, token, { method: "DELETE" }),

  clearHistory: (token: string) =>
    apiFetch<void>("/history", token, { method: "DELETE" }),
};
