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
    apiFetch<Record<string, unknown>>(`/reports/${reportId}`, token),

  createFollowup: (token: string, reportId: string, question: string) =>
    apiFetch<{ answer: string; turn_number: number }>(
      `/reports/${reportId}/followup`,
      token,
      { method: "POST", body: JSON.stringify({ question }) }
    ),

  getHistory: (token: string) =>
    apiFetch<{ reports: unknown[] }>("/history", token),

  deleteReport: (token: string, reportId: string) =>
    apiFetch<void>(`/history/${reportId}`, token, { method: "DELETE" }),

  clearHistory: (token: string) =>
    apiFetch<void>("/history", token, { method: "DELETE" }),
};
