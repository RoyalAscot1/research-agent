const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  createQuery: (query: string) =>
    apiFetch<{ job_id: string; status: string }>("/queries", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  getJobStatus: (jobId: string) =>
    apiFetch<{ job_id: string; status: string; step: string; progress: number }>(
      `/jobs/${jobId}/status`
    ),

  getReport: (reportId: string) =>
    apiFetch<Record<string, unknown>>(`/reports/${reportId}`),

  createFollowup: (reportId: string, question: string) =>
    apiFetch<{ answer: string; turn_number: number }>(
      `/reports/${reportId}/followup`,
      { method: "POST", body: JSON.stringify({ question }) }
    ),

  getHistory: () =>
    apiFetch<{ reports: unknown[] }>("/history"),

  deleteReport: (reportId: string) =>
    apiFetch<void>(`/history/${reportId}`, { method: "DELETE" }),

  clearHistory: () =>
    apiFetch<void>("/history", { method: "DELETE" }),
};
