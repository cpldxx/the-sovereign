/** Typed clients for the two backends. Override URLs with VITE_KG_URL / VITE_HERMES_URL. */

export const KG_API: string = import.meta.env.VITE_KG_URL ?? 'http://localhost:8080';
export const HERMES_API: string = import.meta.env.VITE_HERMES_URL ?? 'http://localhost:8090';

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

export interface DomainDetail {
  domain: string;
  config: { name: string; description: string };
  ontology_generated: boolean;
  stats: { node_count: number; edge_count: number; categories: Record<string, number> };
}

export interface KGNode {
  uid: string;
  domain: string;
  category: string;
  content: string;
  source: string;
  tags: string[];
  reliability: number;
  created_at?: string;
}

export interface KGEdge {
  from_node: string;
  to_node: string;
  relation: string;
  weight: number;
}

export interface GraphNode {
  id: string;
  label: string;
  content: string;
  category: string;
  domain: string;
  source: string;
  tags: string[];
  reliability: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  relation: string;
  weight: number;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface IngestDetail {
  uid: string;
  status: 'stored' | 'rejected';
  reason?: string;
  reliability?: number;
}

export interface IngestResult {
  total: number;
  stored: number;
  rejected: number;
  edges_created: number;
  embedded: number;
  details: IngestDetail[];
}

export interface QueryResult {
  matches: (KGNode & { distance: number })[];
  neighbors: KGNode[];
  edges: KGEdge[];
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

  ingest: (id: string, raw_text: string, source: string) =>
    request<IngestResult>(KG_API, `${d(id)}/ingest`, json({ raw_text, source })),

  query: (id: string, query: string, k: number) =>
    request<QueryResult>(KG_API, `${d(id)}/query`, json({ query, k })),

  ontology: (id: string) =>
    request<{ generated: boolean; ontology: Ontology }>(KG_API, `${d(id)}/ontology`),

  saveOntology: (id: string, ontology: Ontology) =>
    request<{ ontology: Ontology }>(KG_API, `${d(id)}/ontology`, { method: 'PUT', body: JSON.stringify(ontology) }),

  generateOntology: (id: string) => request<unknown>(KG_API, `${d(id)}/ontology/generate`, { method: 'POST' }),
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
  ): Promise<void> {
    let res: Response;
    try {
      res = await fetch(`${HERMES_API}${d(id)}/ask/stream`, {
        ...json({ message, history }),
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
