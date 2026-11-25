import { useEffect, useState } from "react";
import { api, type AnswerResponse, type Hit, type Method } from "./api";

const METHODS: { id: Method; label: string }[] = [
  { id: "hybrid", label: "Hybrid (BM25 + dense, RRF)" },
  { id: "rerank", label: "Hybrid + cross-encoder" },
  { id: "late", label: "Hybrid + late interaction (ColBERT)" },
  { id: "dense", label: "Dense" },
  { id: "finetuned", label: "Dense, fine-tuned embedder" },
  { id: "bm25", label: "BM25" },
];
type Tab = "ask" | "search" | "compare" | "methods";
const TABS: { id: Tab; label: string; help: string }[] = [
  {
    id: "ask",
    label: "Ask",
    help: "Ask a question for one DuckDB version. You get a short answer with the passages it cites, or an abstention when the documentation for that version does not support an answer.",
  },
  {
    id: "search",
    label: "Search",
    help: "Inspect the ranked passages a retrieval method returns. With a reranker (cross-encoder or late interaction) each passage shows how far it moved from its first-stage hybrid rank. Untick 'Version filter' to search every version: passages that do not apply to the selected version are marked red, which is the version confusion the filter prevents.",
  },
  {
    id: "compare",
    label: "Compare versions",
    help: "Run the same query against several versions side by side to see how the documentation changed.",
  },
  {
    id: "methods",
    label: "Compare methods",
    help: "Run the same query for one version through several retrieval methods side by side.",
  },
];

function VersionBadge({ v, compatible }: { v: string; compatible?: boolean | null }) {
  const tone = compatible === false ? "bg-rose-100 text-rose-800 ring-rose-200" : "bg-indigo-50 text-indigo-700 ring-indigo-200";
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}>DuckDB {v}</span>;
}

function RankMove({ base, rank }: { base: number | null | undefined; rank: number }) {
  if (base === undefined) return null;
  if (base === null)
    return <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-500">not in first stage</span>;
  const delta = base - rank;
  const tone = delta > 0 ? "bg-emerald-50 text-emerald-700" : delta < 0 ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-500";
  const move = delta > 0 ? `up ${delta}` : delta < 0 ? `down ${-delta}` : "unchanged";
  return (
    <span className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${tone}`} title="Rank in the first-stage hybrid list before reranking">
      hybrid #{base} → #{rank} ({move})
    </span>
  );
}

function Passage({ hit, rank }: { hit: Hit; rank?: number }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {rank !== undefined && <span className="font-mono text-slate-400">#{rank}</span>}
        <VersionBadge v={hit.version} compatible={hit.compatible} />
        <span className="break-all font-mono">{hit.source_path}</span>
        {rank !== undefined && <RankMove base={hit.base_rank} rank={rank} />}
        {hit.score !== 0 && <span className="ml-auto font-mono">{hit.score.toFixed(3)}</span>}
      </div>
      {hit.section && <p className="mb-1 text-sm font-medium text-slate-700">{hit.section}</p>}
      <p className="line-clamp-5 whitespace-pre-wrap text-sm text-slate-600">{hit.snippet}</p>
      <p className="mt-2 font-mono text-[11px] text-slate-400">{hit.chunk_id}</p>
    </article>
  );
}

// Deep links: ?tab=search&version=1.5&method=rerank&q=...&run=1 opens a view and runs it once the
// backend has answered, so a result can be shared (and checked by a headless browser).
const params = new URLSearchParams(window.location.search);
const initialTab = (TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "ask") as Tab;

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [versions, setVersions] = useState<string[]>([]);
  const [version, setVersion] = useState<string>(params.get("version") ?? "1.5");
  const [method, setMethod] = useState<Method>((params.get("method") as Method | null) ?? "hybrid");
  const [query, setQuery] = useState(params.get("q") ?? "What does date_trunc return when applied to a DATE?");
  const [autoRun, setAutoRun] = useState(params.get("run") === "1");
  const [filterOn, setFilterOn] = useState(params.get("filter") !== "false");
  const [compareVersions, setCompareVersions] = useState<string[]>(["1.4", "1.5", "2.0"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [compare, setCompare] = useState<Record<string, Hit[]> | null>(null);
  const [byMethod, setByMethod] = useState<Record<string, { hits: Hit[]; latency_ms: number }> | null>(null);
  const [compareMethods, setCompareMethods] = useState<Method[]>(["bm25", "dense", "hybrid"]);
  const [available, setAvailable] = useState<Method[] | null>(null);
  const [status, setStatus] = useState<string>("Connecting to the backend…");

  useEffect(() => {
    api.versions().then((r) => setVersions(r.versions.map((v) => v.version))).catch((e) => setError(String(e.message)));
    api
      .health()
      .then((h) => {
        setStatus(`${h.chunks.toLocaleString()} documentation passages indexed`);
        setAvailable(h.methods as Method[]);
      })
      .catch(() => setStatus("Backend unavailable"));
  }, []);

  useEffect(() => {
    if (autoRun && available !== null && versions.length > 0) {
      setAutoRun(false);
      void run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, available, versions]);

  // Only methods the backend reports as built are offered (every UI element backed by the API).
  const methodOptions = METHODS.filter((m) => available === null || available.includes(m.id));

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const v = version === "any" ? null : version;
      if (tab === "ask") setAnswer(await api.answer(query, v, method));
      if (tab === "search") setHits((await api.search(query, v, method, 10, filterOn)).hits);
      if (tab === "compare") setCompare((await api.compare(query, compareVersions, method)).results);
      if (tab === "methods") {
        const runs = await Promise.all(compareMethods.map((m) => api.search(query, v, m, 5)));
        setByMethod(Object.fromEntries(compareMethods.map((m, i) => [m, runs[i]])));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Version-Aware RAG</h1>
          <p className="text-sm text-slate-500">Answers grounded in the DuckDB documentation for the version you choose.</p>
        </div>
        <p className="text-xs text-slate-400">{status}</p>
      </header>

      <nav className="mb-2 flex gap-1 overflow-x-auto rounded-lg bg-slate-200/60 p-1 text-sm">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`flex-1 whitespace-nowrap rounded-md px-3 py-1.5 font-medium ${tab === t.id ? "bg-white shadow-sm" : "text-slate-600 hover:text-slate-900"}`}>
            {t.label}
          </button>
        ))}
      </nav>
      <p className="mb-4 text-sm text-slate-500">{TABS.find((t) => t.id === tab)?.help}</p>

      <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={2}
          className="w-full resize-none rounded-md border border-slate-300 p-3 text-sm focus:border-indigo-500 focus:outline-none" />
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          {tab !== "compare" ? (
            <label className="flex items-center gap-2">Version
              <select value={version} onChange={(e) => setVersion(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1">
                {versions.map((v) => <option key={v} value={v}>{v}</option>)}
                {tab === "methods" && <option value="any">any version</option>}
              </select>
            </label>
          ) : (
            <div className="flex items-center gap-2">Versions
              {versions.map((v) => (
                <label key={v} className="flex items-center gap-1">
                  <input type="checkbox" checked={compareVersions.includes(v)}
                    onChange={(e) => setCompareVersions(e.target.checked ? [...compareVersions, v].sort() : compareVersions.filter((x) => x !== v))} />
                  {v}
                </label>
              ))}
            </div>
          )}
          {tab === "search" && (
            <label className="flex items-center gap-1" title="Only passages that apply to the selected version">
              <input type="checkbox" checked={filterOn} onChange={(e) => setFilterOn(e.target.checked)} />
              Version filter
            </label>
          )}
          {tab === "methods" ? (
            <div className="flex flex-wrap items-center gap-2">Methods
              {methodOptions.map((m) => (
                <label key={m.id} className="flex items-center gap-1">
                  <input type="checkbox" checked={compareMethods.includes(m.id)}
                    onChange={(e) => setCompareMethods(e.target.checked ? [...compareMethods, m.id] : compareMethods.filter((x) => x !== m.id))} />
                  {m.id}
                </label>
              ))}
            </div>
          ) : (
            <label className="flex items-center gap-2">Retrieval
              <select value={method} onChange={(e) => setMethod(e.target.value as Method)} className="rounded-md border border-slate-300 px-2 py-1">
                {methodOptions.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </label>
          )}
          <button onClick={run}
            disabled={busy || query.trim().length < 3 || (tab === "compare" && compareVersions.length === 0) || (tab === "methods" && compareMethods.length === 0)}
            className="ml-auto rounded-md bg-indigo-600 px-4 py-1.5 font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
            {busy ? "Working…" : tab === "ask" ? "Answer" : tab === "search" ? "Search" : "Compare"}
          </button>
        </div>
      </section>

      {busy && (
        <div className="mb-4 animate-pulse rounded-md border border-indigo-100 bg-indigo-50 p-3 text-sm text-indigo-700">
          Retrieving{tab === "ask" ? " and generating an answer" : ""}… rerankers on CPU can take several seconds.
        </div>
      )}
      {error && <div role="alert" className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</div>}

      {tab === "ask" && answer && (
        <section className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-2 flex items-center gap-2">
              <VersionBadge v={answer.version ?? "any"} />
              {answer.abstain && <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">Abstained</span>}
              <span className="ml-auto text-xs text-slate-400">{Math.round(answer.latency_ms)} ms</span>
            </div>
            {answer.abstain ? (
              <p className="text-lg text-slate-700">The documentation for this version does not support an answer.</p>
            ) : (
              <p className="font-mono text-2xl font-semibold text-slate-900">{answer.short_answer}</p>
            )}
            {answer.explanation && <p className="mt-3 text-sm text-slate-600">{answer.explanation}</p>}
            {answer.flags.length > 0 && <p className="mt-2 text-xs text-slate-400">Flags: {answer.flags.join(", ")}</p>}
          </div>
          {answer.citations.length > 0 && (
            <div>
              <h2 className="mb-2 text-sm font-semibold text-slate-700">Cited evidence</h2>
              <div className="grid gap-3 md:grid-cols-2">{answer.citations.map((h) => <Passage key={h.chunk_id} hit={h} />)}</div>
            </div>
          )}
          <details className="rounded-lg border border-slate-200 bg-white p-4">
            <summary className="cursor-pointer text-sm font-medium text-slate-700">Retrieved passages ({answer.retrieved.length})</summary>
            <div className="mt-3 grid gap-3">{answer.retrieved.map((h, i) => <Passage key={h.chunk_id} hit={h} rank={i + 1} />)}</div>
          </details>
        </section>
      )}

      {tab === "search" && hits && (
        <section className="grid gap-3">{hits.length ? hits.map((h, i) => <Passage key={h.chunk_id} hit={h} rank={i + 1} />) : <p className="text-sm text-slate-500">No passages found.</p>}</section>
      )}

      {tab === "compare" && compare && (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(compare).map(([v, hs]) => (
            <div key={v} className="min-w-0 space-y-3">
              <h2 className="text-sm font-semibold text-slate-700">DuckDB {v}</h2>
              {hs.length ? hs.map((h, i) => <Passage key={h.chunk_id} hit={h} rank={i + 1} />) : <p className="text-sm text-slate-500">No passages found for this version.</p>}
            </div>
          ))}
        </section>
      )}

      {tab === "methods" && byMethod && (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(byMethod).map(([m, r]) => (
            <div key={m} className="min-w-0 space-y-3">
              <h2 className="flex items-baseline justify-between gap-2 text-sm font-semibold text-slate-700">
                {METHODS.find((x) => x.id === m)?.label ?? m}
                <span className="font-mono text-xs font-normal text-slate-400">{Math.round(r.latency_ms)} ms</span>
              </h2>
              {r.hits.length ? r.hits.map((h, i) => <Passage key={h.chunk_id} hit={h} rank={i + 1} />) : <p className="text-sm text-slate-500">No passages found.</p>}
            </div>
          ))}
        </section>
      )}

      <footer className="mt-10 text-center text-xs text-slate-400">
        DuckDB documentation © the DuckDB contributors (MIT). Answers cite only documentation passages of the selected version.
      </footer>
    </div>
  );
}
