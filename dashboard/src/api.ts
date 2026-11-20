const TOKEN_KEY = "curiox_token";

export const getToken = () => {
  try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
};
export const setToken = (t: string) => {
  try { localStorage.setItem(TOKEN_KEY, t); } catch { /* storage blocked */ }
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch("/api" + path, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...init.headers },
  });
  if (!r.ok) {
    let detail: unknown = await r.text();
    try { detail = JSON.parse(detail as string).detail; } catch { /* plain text */ }
    throw new Error(`${r.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return r.json();
}

export const post = <T,>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

export type Suggestion = {
  id: number; text: string; strategy: string; language: string | null; quality_flags: string[]; edited: boolean;
};

export type Opportunity = {
  id: number; source: "manual_link" | "own_comment"; url: string | null; platform: string;
  author_handle: string | null; creator: { handle: string; display_name: string | null } | null;
  text: string; parent_text: string | null; language: string | null; relevance_score: number | null;
  status: string; skip_reason: string | null; post_summary: string | null; topics: string[];
  detected_tone: string | null; relevance_reason: string | null; response_strategy: string | null;
  factuality_warnings: string[]; needs_human_review: boolean | null; suggestions: Suggestion[];
  approval: { decision: string; actor: string; final_text: string | null; reason: string | null; created_at: string } | null;
  created_at: string;
};

export type Creator = {
  id: number; platform: string; handle: string; display_name: string | null; topics: string[];
  audience_note: string | null; active: boolean;
};

export type Account = {
  id: number; platform: string; handle: string; status: string; paused_reason: string | null;
  token_expires_at?: string | null;
};

export type Knowledge = { id: number; kind: string; title: string; content: string; tags: string[]; verified: boolean };

export type Overview = {
  counts_by_status: Record<string, number>; approval_rate: number; avg_relevance: number | null;
  languages: Record<string, number>; top_skip_reasons: { reason: string; count: number }[];
  published: number; accounts: Account[];
};

export type WeekRow = { week: string; discovered: number; queued: number; approved: number; rejected: number; published: number };

export type RecentPost = {
  id: number; url: string | null; author_handle: string | null; status: string; skip_reason: string | null;
  relevance_score: number | null; text: string; created_at: string;
};

export type AppConfig = Record<string, unknown>;

export type AuditRow = {
  id: number; created_at: string; actor: string; action: string; entity: string; entity_id: number | null;
  data: Record<string, unknown>;
};
