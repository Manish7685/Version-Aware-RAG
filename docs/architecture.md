# Architecture

The approved design is [`design/RAG-M1_design.md`](design/RAG-M1_design.md). These diagrams show what is
implemented in this repository. Every box names the module that implements it.

## 1. Offline: corpus, indexes and benchmark

```mermaid
flowchart LR
    subgraph Sources["Pinned sources (configs/corpus.toml)"]
        D["DuckDB docs 1.2-2.0<br/>duckdb-web @ pinned commit<br/>(MIT)"]
        RP["17 release posts<br/>(restricted: local only)"]
        RN["4 GitHub release notes<br/>(restricted: local only)"]
    end
    D --> F["corpus fetch / releases fetch<br/>corpus/acquire.py, releases.py"]
    RP --> F
    RN --> F
    F --> P["parse, section tree, chunk<br/>corpus/parse.py, sections.py, chunk.py"]
    P --> B["deterministic build + validate<br/>corpus/build.py, validate.py"]
    B --> M[("data/manifest/<br/>committed, byte-reproducible")]
    B --> C[("data/build/chunks<br/>git-ignored")]
    C -->|"permitted kinds only:<br/>docs_page, migration_guide"| RC["retrieval/corpus.py<br/>version compatibility: corpus/compat.py"]
    RC --> I1["BM25 index (bm25s)"]
    RC --> I2["dense index (bge-small + FAISS)"]
    RC --> I3["fine-tuned dense index<br/>(implemented; model not trained, owner decision)"]
    C --> DR["benchmark drafting (Gemini, permitted passages only)<br/>benchmark/drafting.py + fail-closed privacy audit"]
    DR --> PI["pilot: V1 checks, owner review, split freeze<br/>benchmark/pilot.py, split.py"]
    PI --> S[("benchmark/splits/<br/>dev 15, test 25<br/>(no ci_gate split)")]
    S --> FT["finetune-data (dev only, leakage refused)<br/>retrieval/finetune.py"]
    FT --> I3
```

## 2. Online: query to cited answer

```mermaid
flowchart LR
    U["React UI (frontend/)<br/>Ask, Search, Compare versions, Compare methods"] -->|"/api"| A["FastAPI (api/app.py)<br/>/health /versions /search /answer /compare"]
    A --> VF["version filter<br/>compatible chunks only"]
    VF --> R{"retriever<br/>(one interface)"}
    R --> BM["BM25"]
    R --> DE["dense / fine-tuned"]
    R --> HY["hybrid RRF (k=60)"]
    HY --> RR["cross-encoder rerank (top 50)"]
    HY --> LI["ColBERT MaxSim rerank (top 100)"]
    R --> AP["answer pipeline (answer/pipeline.py)"]
    AP --> AU{"privacy audit<br/>(restricted text, key-like strings)"}
    AU -->|"fails: drop passage,<br/>re-audit, else abstain"| AB["abstain"]
    AU -->|passes| G["Gemini (quota ledger, cache)"]
    G --> V{"valid JSON with citations<br/>to retrieved chunks?"}
    V -->|yes| ANS["short answer + explanation + citations"]
    V -->|no| AB
```

## 3. Evaluation and the CI gate

```mermaid
flowchart LR
    S[("frozen splits<br/>dev 15, test 25")] --> H["harness (retrieval/harness.py)<br/>graded qrels from evidence spans<br/>5 methods x {unfiltered, filtered}"]
    H --> MT["Recall@k, MRR, nDCG, VCR, confuser@10<br/>paired cluster bootstrap CIs, Holm<br/>slices: T1, T2, T3, T4, hard-negative"]
    S --> AE["vrag eval answers<br/>accuracy, version correctness,<br/>abstention P/R, citation precision"]
    AE --> SU["vrag eval support (local NLI)<br/>citation support, faithfulness"]
    GK["vrag eval gate check<br/>(enforced mode)"] -->|"no ci_gate split,<br/>no approved baseline"| ERR["ERROR, exit 2"]
    PL[("pre-review pilot<br/>50 V0 drafts")] --> GD["vrag eval gate --provisional<br/>local demonstration only"]
    GD --> DEMO["neutral: PASS; injected regressions: BLOCK<br/>results/gate/calibration.md, docs/gate_demo/"]
    CI["GitHub Actions eval-gate job"] -.->|"dormant: never ran"| GK
```

Gate state (owner decision, small-pilot scope): the gate logic (`retrieval/gate.py`,
`gate_commands.py`) is implemented and tested, but there is **no separate ci-gate split**, **no
approved baseline** (`configs/gate.toml` has an empty `baseline_sha256`), and the **CI job is
dormant**. The gate was demonstrated only on the pre-review pilot; its tolerances are uncalibrated
design defaults. Since the freeze, the `--provisional` dry run would evaluate the frozen test
split's items, so it is not re-run; the pilot commands are a historical record (`REPRODUCE.md` §6).

## 4. Privacy boundary

Release-post and release-note text is restricted (licence not established). It is fetched and
hash-verified locally, used only for the fail-closed audit that runs before any external request,
and kept in git-ignored paths (`data/cache/`, `data/evidence/local/`). It is never indexed for
retrieval, never sent to Gemini or any other service, never committed, and never copied into the
Docker image (`.dockerignore`).
