/** Typed clients for the two backends. Override URLs with VITE_KG_URL / VITE_HERMES_URL. */

export const KG_API: string = import.meta.env.VITE_KG_URL ?? 'http://localhost:8080';
export const HERMES_API: string = import.meta.env.VITE_HERMES_URL ?? 'http://localhost:8090';
export const RESEARCH_API: string = import.meta.env.VITE_RESEARCH_URL ?? 'http://localhost:8070';
export const LANGFUSE_URL: string = import.meta.env.VITE_LANGFUSE_URL ?? 'http://localhost:3000';
/** Every call carries the session cookie (set by the KG at sign-in, httpOnly — this code never sees it). */
const SIGNED_OUT = 'sovereign:signed-out';

// ── Types ───────────────────────────────────────────────────────────────────

export type Role = 'owner' | 'editor' | 'viewer';

export interface DomainSummary {
  id: string;
  title: string;
  description: string;
  ontology_generated: boolean;
  role: Role;
}

export interface User {
  uid: string;
  email: string;
  name: string;
  admin: boolean;
}

export interface Me {
  kind: 'user';
  user: User;
  roles: Record<string, Role>;
}

export interface Member {
  uid: string;
  email: string;
  name: string;
  role: Role;
  since: string;
}

export interface ApiToken {
  uid: string;
  label: string;
  created_at: string;
  token?: string;  // only right after creation
}

export interface Invite {
  uid: string;
  created_at: string;
  expires_at: string;
  used_by: string | null;
  code?: string;  // only right after creation
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
  config: { name: string; title?: string; description: string };
  ontology_generated: boolean;
  stats: DomainStats;
  role: Role;
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
    chunks?: number;
    seconds?: number;
    timings?: Record<string, number>;
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
  summary: {
    pages_read?: number;
    pages_failed?: number;
    entities_created?: number;
    facts_created?: number;
    facts_strengthened?: number;
    facts_invalidated?: number;
    review_items?: number;
    seconds?: { research: number; ontology: number; ingest: number };
  };
}

export interface ReportStats {
  hours: number;
  changes: {
    sources: number;
    entities_created: number;
    facts_created: number;
    facts_strengthened: number;
    facts_invalidated: number;
    reviews_opened: number;
    reviews_decided: number;
  };
  research: {
    reachable: boolean;
    runs: number;
    failed: number;
    pages_read: number;
    pages_failed: number;
    seconds: { research: number; ontology: number; ingest: number };
  };
  summaries: { candidates: number; refreshed: number };
  llm: { activity: string; calls: number; tokens: number; seconds: number }[] | null;
  ontology_gaps: { kind: 'type' | 'relation'; name: string; count: number }[];
  graph: { entity_count: number; fact_count: number; invalid_fact_count: number; episode_count: number; pending_reviews: number };
  seconds: number;
}

export interface ReportFact {
  uid: string;
  relation: string;
  fact: string;
  source_uid: string;
  source_name: string;
  target_uid: string;
  target_name: string;
  weight: number;
  evidence: number;
  invalid_reason: string | null;
}

/** A daily briefing: what changed in the graph over a period. */
export interface Report {
  uid: string;
  kind: 'daily';
  period_start: string;
  period_end: string;
  created_at: string;
  headline: string;
  briefing: string;
  spoken: string;
  stats: ReportStats;
  digest?: {
    sources: { uid: string; source: string; title: string; content_status: string; entities: number }[];
    entities: { uid: string; name: string; type: string }[];
    facts_created: ReportFact[];
    facts_strengthened: ReportFact[];
    facts_invalidated: ReportFact[];
    reviews_decided: { uid: string; kind: string; status: string; summary: string; resolution: string }[];
    research: { id: string; mode: ResearchMode; question: string; status: string; error: string }[];
  };
}

export interface ActionParam {
  name: string;
  type: 'string' | 'number' | 'integer' | 'boolean';
  description: string;
  required: boolean;
}

/** Something a domain can do: built-in (runs at once) or a user-configured webhook (needs confirmation). */
export interface ActionDef {
  name: string;
  kind: 'builtin' | 'webhook';
  risk: 'internal' | 'external';
  confirm: boolean;
  description: string;
  params: ActionParam[];
  dry_run?: boolean;
  url?: string;
  uid?: string;
}

export type ProposalStatus = 'proposed' | 'executing' | 'executed' | 'rejected' | 'failed' | 'expired';

export interface Proposal {
  uid: string;
  action: string;
  params: Record<string, unknown>;
  rationale: string;
  evidence: string[];
  source: 'head' | 'playbook' | 'user';
  playbook_uid: string | null;
  risk: 'internal' | 'external';
  status: ProposalStatus;
  preview: string;
  result: Record<string, unknown> | null;
  created_at: string;
  expires_at: string;
  decided_at?: string;
  executed_at?: string;
  note?: string;
}

/** A pre-computed "if this happens, do that" rule written from the graph. */
export interface Playbook {
  uid: string;
  name: string;
  situation: string;
  watch: { uid: string; name: string }[];
  response: string;
  action: string;
  evidence: string[];
  status: 'active' | 'retired';
  fired: number;
  last_fired_at: string | null;
  /** Live condition on a sensor, checked around the clock. */
  trigger?: { sensor: string; params: Record<string, unknown>; field: string; op: string; value: number; every_minutes: number } | null;
  trigger_value?: number | null;
  trigger_checked_at?: string | null;
  created_at: string;
  updated_at: string;
}

/** A live-data tool written by the Coder Agent; runs in a sandbox. */
export interface Sensor {
  uid: string;
  name: string;
  description: string;
  params: Record<string, { type: string; description: string; example: unknown }>;
  need: string;
  author: string;
  status: 'active' | 'failed';
  sample: string;
  tested_at: string;
  last_run_at: string | null;
  last_ok: boolean;
  code?: string;
  group?: string;
  kind?: 'api' | 'page';
  host?: string;
}

/** One source of a sensor: an API or a web page opened in a browser. */
export interface SensorSource {
  name: string;
  host: string;
  kind: 'api' | 'page' | 'extract' | 'search';  // extract: read by the model; search: live search (no source yet)
  last_ok: boolean | null;
  score: number;
  avg_seconds: number | null;
  last_error: string | null;
  author: string;
  cooldown_until: string | null;  // resting after the site refused us (bot protection, robots.txt)
}

/** A sensor as agents see it: one need, read from its best working source. */
export interface SensorGroup {
  name: string;
  description: string;
  params: Record<string, { type: string; description: string; example: unknown }>;
  need: string;
  last_ok: boolean;
  last_run_at: string | null;
  working: number;
  sources: SensorSource[];
}

/** A candidate source the Scout tried, and what happened. */
export interface ScoutCandidate {
  host: string;
  kind: 'api' | 'page';
  url: string;
  outcome: string;
  seconds?: number;
  value?: Record<string, unknown>;
}

export interface SensorRequest {
  id: string;
  domain: string;
  need: string;
  backend: string;  // scout | builtin | openhands | openhands→builtin (fell back)
  status: 'queued' | 'planning' | 'collecting' | 'probing' | 'coding' | 'testing' | 'done' | 'failed';
  group?: string | null;
  candidates?: ScoutCandidate[];
  sources?: number | null;
  created_at: string;
  finished_at: string | null;
  seconds: number | null;
  sensor: string | null;
  note: string;
  error: string;
  log: string[];
  result: string | null;
}

export interface SensorReading {
  sensor: string;
  source?: string;
  params: Record<string, unknown>;
  read_at: string;
  ok: boolean;
  result?: unknown;
  error?: string;
  failed_sources?: { source: string; error: string }[];
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

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

/** Called when any request finds the session gone (expired, revoked, signed out elsewhere). */
export function onSignedOut(handler: () => void): () => void {
  addEventListener(SIGNED_OUT, handler);
  return () => removeEventListener(SIGNED_OUT, handler);
}

async function call(url: string, init: RequestInit): Promise<Response> {
  const res = await fetch(url, { ...init, credentials: 'include' });
  if (res.status === 401 && !url.includes('/auth/')) dispatchEvent(new Event(SIGNED_OUT));
  return res;
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await call(`${base}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiError(`Cannot reach ${base} — is the server running?`);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  return body as T;
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
const d = (id: string) => `/domains/${encodeURIComponent(id)}`;

// ── Accounts (KG) ───────────────────────────────────────────────────────────

export const auth = {
  config: () => request<{ signup: 'invite' | 'open'; first_account: boolean }>(KG_API, '/auth/config'),

  me: () => request<Me>(KG_API, '/auth/me'),

  login: (email: string, password: string) =>
    request<{ user: User }>(KG_API, '/auth/login', json({ email, password })).then(r => r.user),

  signup: (email: string, password: string, name: string, invite: string) =>
    request<{ user: User }>(KG_API, '/auth/signup', json({ email, password, name, invite })).then(r => r.user),

  logout: () => request<unknown>(KG_API, '/auth/logout', { method: 'POST' }),

  changePassword: (current: string, next: string) =>
    request<unknown>(KG_API, '/auth/password', json({ current, new: next })),

  tokens: () => request<{ tokens: ApiToken[] }>(KG_API, '/auth/tokens').then(r => r.tokens),

  createToken: (label: string) => request<ApiToken>(KG_API, '/auth/tokens', json({ label })),

  revokeToken: (uid: string) => request<unknown>(KG_API, `/auth/tokens/${encodeURIComponent(uid)}`, { method: 'DELETE' }),

  invites: () => request<{ invites: Invite[]; signup: string }>(KG_API, '/auth/invites'),

  createInvite: () => request<Invite>(KG_API, '/auth/invites', { method: 'POST' }),

  revokeInvite: (uid: string) => request<unknown>(KG_API, `/auth/invites/${encodeURIComponent(uid)}`, { method: 'DELETE' }),
};

// ── KG API ──────────────────────────────────────────────────────────────────

export const kg = {
  health: () => request<{ kg: boolean; arcadedb: boolean; tracing: boolean }>(KG_API, '/health'),

  domains: () => request<{ domains: DomainSummary[] }>(KG_API, '/domains').then(r => r.domains),

  domain: (id: string) => request<DomainDetail>(KG_API, d(id)),

  createDomain: (name: string, description: string) =>
    request<{ domain: string }>(KG_API, '/domains', json({ name, description })).then(r => r.domain),

  deleteDomain: (id: string) => request<unknown>(KG_API, d(id), { method: 'DELETE' }),

  members: (id: string) => request<{ members: Member[] }>(KG_API, `${d(id)}/members`).then(r => r.members),

  share: (id: string, email: string, role: Role) =>
    request<{ members: Member[] }>(KG_API, `${d(id)}/members`, { method: 'PUT', body: JSON.stringify({ email, role }) })
      .then(r => r.members),

  unshare: (id: string, uid: string) =>
    request<{ members: Member[] }>(KG_API, `${d(id)}/members/${encodeURIComponent(uid)}`, { method: 'DELETE' })
      .then(r => r.members),

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

  reports: (id: string, limit = 30) =>
    request<{ reports: Report[]; generating: boolean }>(KG_API, `${d(id)}/reports?limit=${limit}`),

  report: (id: string, uid: string) => request<Report>(KG_API, `${d(id)}/reports/${encodeURIComponent(uid)}`),

  createReport: (id: string, hours = 24) => request<unknown>(KG_API, `${d(id)}/reports`, json({ hours })),

  actions: (id: string) => request<{ actions: ActionDef[] }>(KG_API, `${d(id)}/actions`).then(r => r.actions),

  addAction: (id: string, action: { name: string; description: string; url: string; params: ActionParam[]; dry_run: boolean }) =>
    request<unknown>(KG_API, `${d(id)}/actions`, json(action)),

  removeAction: (id: string, name: string) =>
    request<unknown>(KG_API, `${d(id)}/actions/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  proposals: (id: string, status?: ProposalStatus, limit = 50) =>
    request<{ proposals: Proposal[] }>(KG_API, `${d(id)}/proposals?limit=${limit}${status ? `&status=${status}` : ''}`)
      .then(r => r.proposals),

  decideProposal: (id: string, uid: string, approve: boolean, note = '') =>
    request<Proposal>(KG_API, `${d(id)}/proposals/${encodeURIComponent(uid)}`, json({ approve, note })),

  playbooks: (id: string) =>
    request<{ playbooks: Playbook[]; running: boolean }>(KG_API, `${d(id)}/playbooks`),

  runPlaybooks: (id: string) => request<unknown>(KG_API, `${d(id)}/playbooks/cycle`, json({ hours: 24 })),

  sensors: (id: string) =>
    request<{ groups: SensorGroup[]; sensors: Sensor[]; requests: SensorRequest[] }>(KG_API, `${d(id)}/sensors`),

  sensor: (id: string, name: string) => request<Sensor>(KG_API, `${d(id)}/sensors/${encodeURIComponent(name)}`),

  readSensor: (id: string, name: string, params: Record<string, unknown>) =>
    request<SensorReading>(KG_API, `${d(id)}/sensors/${encodeURIComponent(name)}/read`, json({ params })),

  removeSensor: (id: string, name: string) =>
    request<unknown>(KG_API, `${d(id)}/sensors/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  requestSensor: (id: string, need: string, backend?: 'scout' | 'builtin' | 'openhands', group?: string) =>
    request<SensorRequest>(KG_API, `${d(id)}/sensors`, json({ need, backend, group })),

  checkSensors: (id: string) =>
    request<{ sensors: Record<string, { working: number; sources: number; rescout?: string }> }>(
      KG_API, `${d(id)}/sensors/check`, { method: 'POST' }),

  retirePlaybook: (id: string, uid: string) =>
    request<unknown>(KG_API, `${d(id)}/playbooks/${encodeURIComponent(uid)}`, {
      method: 'PATCH', body: JSON.stringify({ status: 'retired' }),
    }),
};

// ── Research (DeerFlow) ─────────────────────────────────────────────────────

export const research = {
  health: () => request<{ research: boolean; model: string; searxng: boolean; crawler: boolean; nightly: string; tracing: boolean }>(
    RESEARCH_API, '/health'),

  start: (id: string, mode: ResearchMode, question = '') =>
    request<ResearchJob>(RESEARCH_API, `${d(id)}/research`, json({ mode, question })),

  jobs: (id: string) =>
    request<{ jobs: ResearchJob[] }>(RESEARCH_API, `/jobs?domain=${encodeURIComponent(id)}&limit=30`).then(r => r.jobs),

  job: (jobId: string) => request<ResearchJob>(RESEARCH_API, `/jobs/${encodeURIComponent(jobId)}`),
};

// ── Hermes (Head Agent) ─────────────────────────────────────────────────────

export const hermes = {
  health: () => request<{ hermes: boolean; model: string; tracing: boolean; voice?: { stt: string; tts: string } }>(
    HERMES_API, '/health'),

  /** Load the speech model and the Head's LLM before the first spoken question. */
  warm: (id: string) => request<unknown>(HERMES_API, `/voice/warm?domain=${encodeURIComponent(id)}`, { method: 'POST' }),

  /** Speech → text on this machine (local Whisper). */
  async transcribe(audio: Blob, language?: string): Promise<{ text: string; language: string; seconds: number }> {
    const res = await call(`${HERMES_API}/voice/transcribe${language ? `?language=${language}` : ''}`, {
      method: 'POST', body: audio, headers: { 'Content-Type': audio.type || 'application/octet-stream' },
    }).catch(() => { throw new ApiError('Cannot reach Hermes (port 8090) for speech'); });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(body.detail ?? `HTTP ${res.status}`);
    return body;
  },

  /** Text → spoken audio (local voices by default; markdown, URLs and uids left out). */
  async speak(text: string): Promise<Blob> {
    const res = await call(`${HERMES_API}/voice/speak`, {
      ...json({ text }), headers: { 'Content-Type': 'application/json' },
    }).catch(() => { throw new ApiError('Cannot reach Hermes (port 8090) for speech'); });
    if (!res.ok) throw new ApiError(`Speech failed: HTTP ${res.status}`);
    return res.blob();
  },

  /** Streams Head Agent progress; resolves when the final `answer` or `error` event arrives. */
  async askStream(
    id: string,
    message: string,
    history: Turn[],
    onEvent: (e: AgentEvent) => void,
    signal?: AbortSignal,
    maxIterations = 8,
    voice = false,
  ): Promise<void> {
    let res: Response;
    try {
      res = await call(`${HERMES_API}${d(id)}/ask/stream`, {
        ...json({ message, history, max_iterations: maxIterations, voice }),
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
