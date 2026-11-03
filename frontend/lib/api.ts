// Typed client for the FastAPI backend (backend/app/main.py). Every call goes through the
// same-origin proxy in next.config.ts. Shapes mirror the dataclasses in backend/app/agent.

const BASE = "/api/be";

export type ApprovalAction = "book" | "cancel" | "reschedule";

/** approvals.render_card(): built by the backend from stored data, never from model text. */
export type ApprovalCard = {
  approval_id: string;
  action: ApprovalAction;
  title: string;
  /**
   * book: [provider (service), when, "Service ₹x", "GST (18%) ₹y", ..., "Total ₹n"] (itemised only when there are fees)
   * cancel: [provider (service), when, ₹price]; reschedule: [provider, From …, To …]
   */
  lines: string[];
  expires_at: string;
  buttons: string[];
  /** book only: what the checkout itemises. */
  breakdown?: { label: string; amount: number }[];
  /** book only: the amount to pay, fees included. */
  total?: number;
};

/** memory.after_booking(): "Want me to remember X?" */
export type MemoryPrompt = {
  memory_id: string;
  key: string;
  value: unknown;
  text: string;
  buttons: string[];
};

/** orchestrator.ChatResult */
export type ChatResult = {
  session_id: string | null;
  reply: string;
  /** INTAKE | SEARCH | PICK | HOLD | AWAIT_APPROVAL | EXECUTE | RECONCILE | DONE | FAILED */
  state: string | null;
  run_id: string | null;
  exit_reason: string | null;
  approval_card: ApprovalCard | null;
  /** Code-rendered output shown as-is (e.g. the "what I remember" text). Strings in practice. */
  user_views: unknown[];
  /** Reconcile / proactive messages that arrived while the user was away. */
  notices: string[];
  busy: boolean;
  memory_prompt: MemoryPrompt | null;
  /** Conversation title, set by the backend (added with the /conversations endpoints). */
  title?: string | null;
};

export type Notification = {
  id: string;
  user_id: string;
  session_id: string | null;
  event_id: string | null;
  body: string;
  created_at: string;
  read_at: string | null;
  urgency: string;
};

export type Memory = {
  id: string;
  key: string;
  value: unknown;
  kind: "explicit" | "inferred";
  status: "active" | "pending_confirmation" | string;
  confidence: number | null;
  updated_at: string | null;
};

export type Span = {
  id?: string;
  kind: "model_call" | "tool_call" | "state_transition" | "context" | "approval" | string;
  name: string;
  latency_ms?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null;
  error?: string | null;
  input?: unknown;
  /** tool_call spans carry the tool's result, e.g. the provider search matches. */
  output?: unknown;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Can't reach the Errand backend. Is it running?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body) });

const qs = (o: Record<string, string | boolean | undefined>) =>
  Object.entries(o)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");

export const api = {
  health: () => call<{ status: string }>("/health"),

  chat: (userId: string, message: string, sessionId: string | null) =>
    post<ChatResult>("/chat", { user_id: userId, message, session_id: sessionId }),

  approve: (userId: string, approvalId: string) =>
    post<ChatResult>(`/approvals/${approvalId}/approve`, { user_id: userId }),

  reject: (userId: string, approvalId: string) =>
    post<ChatResult>(`/approvals/${approvalId}/reject`, { user_id: userId }),

  session: (userId: string, sessionId: string) =>
    call<{ state: string; approval_card: ApprovalCard | null }>(
      `/sessions/${sessionId}?${qs({ user_id: userId })}`,
    ),

  /** Reading notifications also nudges the backend to process anything that happened while away. */
  notifications: (userId: string, unreadOnly = false) =>
    call<{ notifications: Notification[] }>(
      `/notifications?${qs({ user_id: userId, unread_only: unreadOnly })}`,
    ),

  readNotification: (userId: string, id: string) =>
    post<{ ok: boolean }>(`/notifications/${id}/read`, { user_id: userId }),

  memories: (userId: string) =>
    call<{ memories: Memory[] }>(`/memories?${qs({ user_id: userId })}`),

  confirmMemory: (userId: string, id: string) =>
    post<{ ok: boolean }>(`/memories/${id}/confirm`, { user_id: userId }),

  rejectMemory: (userId: string, id: string) =>
    post<{ ok: boolean }>(`/memories/${id}/reject`, { user_id: userId }),

  forgetMemory: (userId: string, id: string) =>
    call<{ ok: boolean }>(`/memories/${id}?${qs({ user_id: userId })}`, { method: "DELETE" }),

  trace: (runId: string) =>
    call<{ run: Record<string, unknown>; spans: Span[] }>(`/runs/${runId}/trace`),
};
