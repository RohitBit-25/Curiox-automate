const TOKEN_KEY = "curiox_token";

export const getToken = () => {
  try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
};
export const setToken = (t: string) => {
  try { localStorage.setItem(TOKEN_KEY, t); } catch { /* storage blocked */ }
};

/** Error carrying the API's machine-readable code (e.g. llm_not_configured) when there is one. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public detail?: unknown) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch("/api" + path, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...init.headers },
  });
  if (!r.ok) {
    const raw = await r.text();
    let detail: unknown = raw;
    try { detail = JSON.parse(raw).detail; } catch { /* plain text */ }
    const d = detail as { code?: string; message?: string; issues?: string[]; reason?: string } | string;
    const msg = typeof d === "string" ? d
      : d?.message ? d.message + (d.issues ? `: ${d.issues.join(", ")}` : d.reason ? `: ${d.reason}` : "")
      : JSON.stringify(d);
    throw new ApiError(r.status, msg || `Request failed (${r.status})`, typeof d === "object" ? d?.code : undefined, d);
  }
  return r.json();
}

export const api = <T,>(path: string) => request<T>(path);
export const post = <T,>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T,>(path: string) => request<T>(path, { method: "DELETE" });
export const upload = <T,>(path: string, file: File) =>
  request<T>(path, { method: "PUT", body: file, headers: { "Content-Type": file.type } });

// ---------- types ----------
export type Mode = "off" | "draft" | "auto";

export type Status = {
  llm: { configured: boolean; provider: string; model: string };
  instagram: { connected: boolean; active: boolean; handle: string | null; paused_reason: string | null; token_expires_at: string | null };
  webhooks: { configured: boolean };
  publishing: { configured: boolean };
  automation_paused: boolean;
  safety: Record<string, unknown>;
};

export type Pillar = { name: string; description: string; share: number };
export type Voice = {
  handle: string; who: string; audience: string; offer: string; pillars: Pillar[]; always: string[]; never: string[];
  examples: string[]; cta_link: string; languages: string[]; best_times: string[];
};
export type AppSettings = {
  mode_comments: Mode; mode_dms: Mode; auto_min_confidence: number; cap_auto_comment_replies_per_hour: number;
  cap_auto_dms_per_hour: number; cap_funnel_dms_per_hour: number; engagement_daily_target: number; voice: Voice;
};

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
  auto: { bucket: string; confidence: number; reason: string; blocked: string | null } | null;
  approval: { decision: string; actor: string; final_text: string | null; reason: string | null; created_at: string } | null;
  created_at: string;
};

export type Creator = {
  id: number; platform: string; handle: string; display_name: string | null; topics: string[];
  audience_note: string | null; active: boolean; last_engaged_at: string | null;
  followed_at: string | null; followed_back: boolean | null;
};
export type BulkResult = { added: string[]; skipped: string[]; researching?: boolean };
export type FetchResult = { status: "ok" | "no_permission" | "no_account"; new: number; failed: string[]; error?: string };
export type FollowRow = { kind: "creator" | "lead"; id: number; handle: string; name: string | null; followed_at: string | null; followed_back: boolean | null };
export type FollowList = { daily_limit: number; followed_today: number; to_follow: FollowRow[]; check_back: FollowRow[]; followed_back: number; checked: number };
export type Account = {
  id: number; platform: string; handle: string; status: string; paused_reason: string | null; token_expires_at?: string | null;
};
export type Knowledge = { id: number; kind: string; title: string; content: string; tags: string[]; verified: boolean };
export type AuditRow = {
  id: number; created_at: string; actor: string; action: string; entity: string; entity_id: number | null;
  data: Record<string, unknown>;
};

export type CheckResult = { check: string; status: "PASS" | "WARN" | "FAIL"; detail: string };
export type CaptionLint = { visible: string; truncated: boolean; characters: number; checks: CheckResult[]; verdict: string };
export type HookScore = {
  spoken: string; on_screen: string; formula: string; score: number; verdict: "STRONG" | "OK" | "WEAK";
  flags: string[]; weakest: string; checks: Record<string, { score: number; detail: string }>;
  classified: { id: number; name: string } | null;
};
export type Tells = { human_score: number; verdict: "PASS" | "REVIEW" | "FLAGGED" };
export type ContentFormat = "reel" | "carousel" | "image" | "story";
export type ContentItem = {
  id: number; week_start: string; format: ContentFormat; pillar: string; post_type: string; goal: string; angle: string;
  hook_formula: string | null; brief: string; scheduled_at: string | null; status: string; caption: string;
  hashtags: string[]; media: { file: string; kind: "image" | "video"; url: string }[]; error: string | null;
  permalink: string | null; approved_by: string | null; published_at: string | null;
  insights: Record<string, number | null> | null;
  draft: null | {
    plan_why?: string; hook_scores?: HookScore[]; caption_lint?: CaptionLint | null; tells?: Tells | null;
    hashtag_issues?: string[]; cta?: string; notes?: string;
    beats?: { t: string; say: string; show: string; on_screen: string }[];
    slides?: { headline: string; body: string }[]; frames?: { text: string }[];
    hashtags?: { tag: string; tier: string }[];
  };
};

export type FunnelRule = {
  id: number; name: string; keywords: string[]; media_id: string | null; dm_text: string; public_replies: string[];
  active: boolean; sent_7d: number; sent_total: number; failed_total: number;
};
export type FunnelSend = {
  id: number; post_id: number; commenter_id: string; status: string; error: string | null;
  public_reply_text: string | null; created_at: string;
};
export type IgMedia = {
  id: string; caption?: string; permalink?: string; timestamp?: string; media_type?: string;
  thumbnail_url?: string; media_url?: string;
};

export type Message = {
  id: number; direction: "in" | "out"; text: string; status: string; actor: string | null; error: string | null;
  meta: null | { category?: string; confidence?: number; needs_human_reason?: string; lead_signal?: boolean;
    lead_summary?: string; blocked?: string | null };
  created_at: string;
};
export type Conversation = {
  id: number; igsid: string; username: string | null; needs_reply: boolean; last_message_at: string;
  last_inbound_at: string | null; window_open: boolean; lead: { id: number; handle: string; stage: string } | null;
  preview: string; draft_id: number | null; messages?: Message[];
};

export type LeadStage = "new" | "researched" | "contacted" | "replied" | "call" | "won" | "lost";
export type Lead = {
  id: number; handle: string; name: string | null; niche: string | null; website: string | null; notes: string | null;
  profile_text: string | null; stage: LeadStage; rubric_total: number | null; pitch: string | null;
  followups: string[]; followups_sent: number; contacted_at: string | null; next_followup_at: string | null;
  followed_at: string | null; followed_back: boolean | null;
  conversation_id: number | null;
  research: null | {
    summary: string; best_angle: string; confidence: number; insufficient_info: boolean;
    rubric: { id: string; score: number; note: string }[];
    problems: { problem: string; evidence: string; curiox_solution: string; impact: "high" | "medium" | "low" }[];
  };
};

export type DailyTask = {
  id: number; kind: "creator" | "opportunity"; done_at: string | null; creator: Creator | null; opportunity: Opportunity | null;
};
export type EngagementToday = { day: string; done: number; target: number; streak: number; tasks: DailyTask[] };

export type Alert = { level: "info" | "warn" | "bad"; code: string; text: string; href: string };
export type Today = {
  approvals: { comments: number; content: number; dms: number };
  engagement: { done: number; target: number; streak: number };
  scheduled: { id: number; format: ContentFormat; pillar: string; angle: string; status: string; scheduled_at: string; has_media: boolean }[];
  inbox: { needs_reply: number };
  followups_due: { id: number; handle: string; stage: string; text: string }[];
  alerts: Alert[];
  kpis: { followers: number | null; followers_delta_7d: number | null; reach_7d: number | null; er_7d: number | null; followers_spark: number[] };
  modes: { comments: Mode; dms: Mode };
};

export type GrowthRow = { day: string; followers: number | null; reach: number | null; views: number | null; er: number | null };
export type ContentStats = {
  items: { id: number; format: string; pillar: string; angle: string; published_at: string; permalink: string | null;
    reach: number | null; saved: number | null; shares: number | null; er: number | null }[];
  by_pillar: { key: string; posts: number; avg_reach: number | null; avg_er: number | null }[];
  by_format: { key: string; posts: number; avg_reach: number | null; avg_er: number | null }[];
};
export type WeekRow = { week: string; discovered: number; queued: number; approved: number; rejected: number; published: number };
export type RecentPost = {
  id: number; url: string | null; author_handle: string | null; status: string; skip_reason: string | null;
  relevance_score: number | null; text: string; created_at: string;
};
