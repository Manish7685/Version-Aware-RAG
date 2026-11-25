// Typed client for the FastAPI backend. In development Vite proxies /api to uvicorn; in
// production the backend serves this app and the API from the same origin.
const BASE = import.meta.env.DEV ? "/api" : "";

export type Method = "bm25" | "dense" | "finetuned" | "hybrid" | "rerank" | "late";

export interface Hit {
  chunk_id: string;
  version: string;
  source_path: string;
  section: string;
  snippet: string;
  score: number;
  compatible: boolean | null;
  base_rank?: number | null; // rank in the first-stage (hybrid) list, for rerank and late
}

export interface SearchResponse {
  hits: Hit[];
  latency_ms: number;
  first_stage: string | null;
}

export interface AnswerResponse {
  question: string;
  version: string | null;
  abstain: boolean;
  short_answer: string;
  explanation: string;
  citations: Hit[];
  retrieved: Hit[];
  flags: string[];
  latency_ms: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    // FastAPI validation errors (422) carry a list of {loc, msg}; other errors a string.
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg ?? "invalid input").join("; ")
          : `Request failed (${res.status})`;
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  versions: () => request<{ versions: { version: string; package: string }[] }>("/versions"),
  health: () => request<{ status: string; chunks: number; methods: string[] }>("/health"),
  search: (q: string, version: string | null, method: Method, k = 10, filter = true) =>
    request<SearchResponse>(
      `/search?${new URLSearchParams({ q, method, k: String(k), ...(version ? { version } : {}), ...(filter ? {} : { filter: "false" }) })}`,
    ),
  answer: (question: string, version: string | null, method: Method) =>
    request<AnswerResponse>("/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, version, method }),
    }),
  compare: (q: string, versions: string[], method: Method) =>
    request<{ results: Record<string, Hit[]> }>(
      `/compare?${new URLSearchParams({ q, versions: versions.join(","), method })}`,
    ),
};
