/** Typed clients for the two backends. Override URLs with VITE_KG_URL / VITE_HERMES_URL. */

export const KG_API: string = import.meta.env.VITE_KG_URL ?? 'http://localhost:8080';
export const HERMES_API: string = import.meta.env.VITE_HERMES_URL ?? 'http://localhost:8090';
export const RESEARCH_API: string = import.meta.env.VITE_RESEARCH_URL ?? 'http://localhost:8070';

// ── Types ───────────────────────────────────────────────────────────────────

export interface DomainSummary {
  id: string;
  description: string;
  ontology_generated: boolean;
}

export interface Ontology {
  entity_types: string[];
  relation_types: string[];
}

export interface DomainStats {
  entity_count: number;
  fact_count: number;
  invalid_fact_count: number;
  episode_count: number;
  pending_reviews: number;
  categories: Record<string, number>;
}

export interface DomainDetail {
  domain: string;
  config: { name: string; description: string };
  ontology_generated: boolean;
  stats: DomainStats;
}

/** A neuron: one real-world thing. */
export interface Entity {
  uid: string;
  name: string;
  type: string;
  summary: string;
  aliases: string[] | null;
  mentions: number | null;
  created_at?: string;
  updated_at?: string;
  similarity?: number;
}

/** A synapse: a sourced statement connecting two entities (or one, for has_state). */
export interface Fact {
  uid: string;
  relation: string;
  fact: string;
  source_uid: string;
  source_name: string;
  target_uid: string;
  target_name: string;
  weight: number;
  evidence: number;
  sources: string[];
  valid: boolean;
  invalid_at?: string | null;
  invalid_reason?: string | null;
  created_at?: string;
  updated_at?: string;
  similarity?: number;
}

/** A memory: one ingested source. */
export interface Episode {
  uid: string;
  source: string;
  title: string;
  content_status: 'full' | 'partial' | string;
  created_at: string;
  snippet?: string;
  entities?: number;
}

export interface Graph {
  entities: Entity[];
  facts: Fact[];
}

export interface EntityDetail {
  entity: Entity;
  facts: Fact[];
  episodes: Episode[];
}

export interface IngestEntityItem {
  name: string;
  type: string;
  uid?: string;
  status: 'created' | 'matched' | 'dropped';
  reason?: string;
}

export interface IngestFactItem {
  fact: string;
  relation: string;
  source: string;
  target: string;
  uid?: string;
  status: 'created' | 'strengthened' | 'rejected' | 'review';
  reason?: string;
  weight?: number;
  evidence?: number;
  invalidated?: string[];
}

export interface IngestResult {
  episode_uid: string;
  duplicate: boolean;
  entities_created: number;
  entities_matched: number;
  entities_dropped: number;
  facts_created: number;
  facts_strengthened: number;
  facts_invalidated: number;
  facts_rejected: number;
  facts_review: number;
  review_items: number;
  touched_uids: string[];
  entities: IngestEntityItem[];
  facts: IngestFactItem[];
}

export interface QueryResult {
  entities: Entity[];
  facts: Fact[];
  episodes: Episode[];
}

export interface Review {
  uid: string;
  kind: 'fact' | 'merge' | 'link';
  status: 'pending' | 'approved' | 'rejected';
  summary: string;
  payload: Record<string, unknown>;
  created_at: string;
  resolution?: string;
}

export type ResearchMode = 'bootstrap' | 'update' | 'mission';

export interface ResearchPage {
  url: string;
  title: string;
  status: 'full' | 'partial' | 'failed';
  error: string;
  chars: number;
  ingest?: {
    skipped?: string;
    error?: string;
    duplicate?: boolean;
    entities_created?: number;
    entities_matched?: number;
    facts_created?: number;
    facts_strengthened?: number;
    facts_invalidated?: number;
    facts_rejected?: number;
    review_items?: number;
  };
}

export interface ResearchJob {
  id: string;
  domain: string;
  mode: ResearchMode;
  question: string;
  status: 'queued' | 'researching' | 'ontology' | 'ingesting' | 'done' | 'failed';
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  steps: { at: string; text: string }[];
  pages?: ResearchPage[];
  report: string;
  error: string;
  summary: Record<string, number>;
}

export interface Turn {
  role: 'user' | 'assistant';
  content: string;
}

export type AgentEvent =
  | { type: 'tool_start'; id: string; name: string; args: unknown }
  | { type: 'tool_end'; id: string; name: string; uids: string[]; result: string }
  | { type: 'delta'; text: string }
  | { type: 'answer'; text: string }
  | { type: 'error'; detail: string };

// ── HTTP ────────────────────────────────────────────────────────────────────

export class ApiError extends Error {}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiError(`Cannot reach ${base} — is the server running?`);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    throw new ApiError(detail || `HTTP ${res.status}`);
  }
  return body as T;
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
const d = (id: string) => `/domains/${encodeURIComponent(id)}`;

// ── KG API ──────────────────────────────────────────────────────────────────

export const kg = {
  health: () => request<{ kg: boolean; arcadedb: boolean }>(KG_API, '/health'),

  domains: () => request<{ domains: DomainSummary[] }>(KG_API, '/domains').then(r => r.domains),

  domain: (id: string) => request<DomainDetail>(KG_API, d(id)),

  createDomain: (name: string, description: string) =>
    request<{ domain: string }>(KG_API, '/domains', json({ name, description })).then(r => r.domain),

  deleteDomain: (id: string) => request<unknown>(KG_API, d(id), { method: 'DELETE' }),

  graph: (id: string) => request<Graph>(KG_API, `/graph/${encodeURIComponent(id)}`),

  ingest: (id: string, raw_text: string, source: string, title?: string, content_status: 'full' | 'partial' = 'full') =>
    request<IngestResult>(KG_API, `${d(id)}/ingest`, json({ raw_text, source, title, content_status })),

  entity: (id: string, uid: string) => request<EntityDetail>(KG_API, `${d(id)}/entities/${encodeURIComponent(uid)}`),

  episodes: (id: string, limit = 50) =>
    request<{ episodes: Episode[] }>(KG_API, `${d(id)}/episodes?limit=${limit}`).then(r => r.episodes),

  reviews: (id: string, status: 'pending' | 'approved' | 'rejected' = 'pending') =>
    request<{ reviews: Review[] }>(KG_API, `${d(id)}/reviews?status=${status}`).then(r => r.reviews),

  decide: (id: string, uid: string, approve: boolean, note = '') =>
    request<{ resolution: string }>(KG_API, `${d(id)}/reviews/${encodeURIComponent(uid)}`, json({ approve, note })),

  query: (id: string, query: string, k: number) =>
    request<QueryResult>(KG_API, `${d(id)}/query`, json({ query, k })),

  ontology: (id: string) =>
    request<{ generated: boolean; ontology: Ontology }>(KG_API, `${d(id)}/ontology`),

  saveOntology: (id: string, ontology: Ontology) =>
    request<{ ontology: Ontology }>(KG_API, `${d(id)}/ontology`, { method: 'PUT', body: JSON.stringify(ontology) }),

  generateOntology: (id: string) => request<unknown>(KG_API, `${d(id)}/ontology/generate`, { method: 'POST' }),
};

// ── Research (DeerFlow) ─────────────────────────────────────────────────────

export const research = {
  health: () => request<{ research: boolean; model: string; searxng: boolean; crawler: boolean; nightly: string }>(
    RESEARCH_API, '/health'),

  start: (id: string, mode: ResearchMode, question = '') =>
    request<ResearchJob>(RESEARCH_API, `${d(id)}/research`, json({ mode, question })),

  jobs: (id: string) =>
    request<{ jobs: ResearchJob[] }>(RESEARCH_API, `/jobs?domain=${encodeURIComponent(id)}&limit=30`).then(r => r.jobs),

  job: (jobId: string) => request<ResearchJob>(RESEARCH_API, `/jobs/${encodeURIComponent(jobId)}`),
};

// ── Hermes (Head Agent) ─────────────────────────────────────────────────────

export const hermes = {
  health: () => request<{ hermes: boolean; model: string }>(HERMES_API, '/health'),

  /** Streams Head Agent progress; resolves when the final `answer` or `error` event arrives. */
  async askStream(
    id: string,
    message: string,
    history: Turn[],
    onEvent: (e: AgentEvent) => void,
    signal?: AbortSignal,
    maxIterations = 8,
  ): Promise<void> {
    let res: Response;
    try {
      res = await fetch(`${HERMES_API}${d(id)}/ask/stream`, {
        ...json({ message, history, max_iterations: maxIterations }),
        headers: { 'Content-Type': 'application/json' },
        signal,
      });
    } catch (e) {
      if (signal?.aborted) throw e;
      throw new ApiError('Cannot reach the Head Agent — is Hermes (port 8090) running?');
    }
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(body.detail ?? `HTTP ${res.status}`);
    }

    // Server-Sent Events: "data: {json}\n\n" frames.
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = frame.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('');
        if (data) onEvent(JSON.parse(data) as AgentEvent);
      }
    }
  },
};
