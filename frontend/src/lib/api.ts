/** Typed clients for the two backends. Override URLs with VITE_KG_URL / VITE_HERMES_URL. */

export const KG_API: string = import.meta.env.VITE_KG_URL ?? 'http://localhost:8080';
export const HERMES_API: string = import.meta.env.VITE_HERMES_URL ?? 'http://localhost:8090';
export const RESEARCH_API: string = import.meta.env.VITE_RESEARCH_URL ?? 'http://localhost:8070';
// Unset (dev) → the local LangFuse; '' (the Docker build without one) → no link at all — '' used to open the app itself.
export const LANGFUSE_URL: string = (import.meta.env.VITE_LANGFUSE_URL ?? 'http://localhost:3000').trim();
/** Every call carries the session cookie (set by the KG at sign-in, httpOnly — this code never sees it). */
const SIGNED_OUT = 'sovereign:signed-out';

// ── Types ───────────────────────────────────────────────────────────────────

export type Role = 'owner' | 'editor' | 'viewer';

const RANK: Record<Role, number> = { viewer: 1, editor: 2, owner: 3 };
/** Whether `role` may do what needs `need` (the KG checks it too; the UI only hides what would answer 403). */
export const can = (role: Role | undefined, need: Role) => !!role && RANK[role] >= RANK[need];

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

// ── Live activity (the agent view) ─────────────────────────────────────────

export interface ActivityEvent {
  seq: number;
  at: string;
  source: string;   // an agent's name, "head", "graph" or "user"
  target: string;
  kind: string;     // task · question · answer · report · notice · note · tool · write
  text: string;
}

export interface Activity {
  seq: number;
  working: { agent: string; since: string; doing: string[] }[];
  events: ActivityEvent[];
}

// ── The Head's team (crew) ──────────────────────────────────────────────────

export interface AgentTask {
  name: string;
  description: string;
  background: boolean;
  inputs: Record<string, string>;
}

export interface TeamAgent {
  name: string;
  title: string;
  role: string;
  tasks: AgentTask[];
  state: 'working' | 'queued' | 'idle';
  queued: number;
}

/** How a domain learns by itself (kg/core/learning.py). */
export interface LearningSettings {
  enabled: boolean;
  times: string[];          // HH:MM in `timezone`
  timezone: string;
  curiosity: number;        // research questions it pursues per cycle
  forget_after_days: number; // 0: never
}

export interface LearningCycle {
  slot: string;
  started_at: string;
  finished_at?: string;
  questions: { question: string; why: string }[];
  runs: { id: string; mode: string; question: string; status: string; error: string;
          summary: { pages_read?: number; facts_created?: number; facts_strengthened?: number; facts_invalidated?: number } }[];
  forgotten?: { facts: number; entities: number };
  exam?: { score: number; answered: number; partial: number; open: number; total: number };
}

export interface LearningGaps {
  exam: string[];            // the charter's exam questions the graph couldn't answer at its last sitting
  weak: { entity: string; weak_facts: number }[];
  stale: { entity: string; facts: number; last: string }[];
  unanswered: string[];      // the caller's own questions the graph couldn't answer
  unanswered_total: number;  // everyone's (each person's chat is theirs alone)
  ontology: string[];
}

/** What the user wants from a domain (kg/core/charter.py): drafted from the Head's interview, then it steers all research. */
export type CharterDepth = 'overview' | 'working' | 'expert';

export interface Charter {
  goal: string;
  questions: string[];   // the core questions, most important first
  scope: string[];
  exclude: string[];
  depth: CharterDepth;
}

export interface CharterSuggestion {
  uid: string;
  kind: 'question' | 'scope' | 'exclude';
  text: string;
  why: string;
  at: string;
}

export interface CharterState {
  charter: Charter | null;
  status: 'none' | 'draft' | 'active';
  suggestions: CharterSuggestion[];
  updated_at: string | null;
  confirmed_at: string | null;
  kickoff?: { jobs?: { id: string; mode: string; question: string }[]; error?: string } | null;
}

/** How much of the charter the graph can answer (kg/core/exam.py): questions drawn from the charter, answered from the
 * graph alone, graded 2 / 1 / 0. */
export interface ExamItem {
  id: string;
  core: number;          // 1-based: which core question it tests
  tier?: 'basic' | 'working' | 'deep';
  question: string;
  points: 0 | 1 | 2;
  reference: string;     // the answer as the exam's source states it
  source: { url: string; title: string } | null;   // held out: research never learns from it
  conflict: boolean;     // the graph's facts contradict the reference
  answer: string;        // from the graph's facts only
  facts: string[];
  sources: number;
  well_sourced: boolean; // a cited fact has more than one source
}

export interface ExamCore {
  core: number;
  question: string;
  score: number | null;   // null: no source was found to write its questions from
  status: 'answered' | 'partial' | 'open' | 'unset';
}

export interface ExamSitting {
  uid: string;
  created_at: string;
  version: number;
  score: number;
  answered: number;
  partial: number;
  open: number;
  total: number;
  graph: { entities: number; facts: number };
}

export interface ExamRun extends ExamSitting {
  conflicts?: number;
  items: ExamItem[];
  cores: ExamCore[];
  seconds: number;
}

export interface ExamState {
  exam: { version: number; created_at: string; cores: string[]; unsourced?: number[];
          questions: { id: string; core: number; tier?: string; question: string; answer?: string; source?: { url: string; title: string } }[] } | null;
  latest: ExamRun | null;
  history: ExamSitting[];   // oldest first
  running: boolean;
}

/** The living review of one core question (kg/core/synthesis.py): every point rests on the graph facts it cites. */
export interface SynthesisPoint {
  text: string;
  facts: string[];
}

export interface SynthesisPage {
  core: number;
  question: string;
  summary: string;          // '' when the graph knows nothing about it yet
  consensus: SynthesisPoint[];
  debates: SynthesisPoint[];
  open: string[];
  sources: { title: string; url: string; tier: string; facts: number }[];
  facts: number;            // facts it was written from
  written_at: string;
}

export interface ChatTurn {
  uid: string;
  role: 'user' | 'assistant';
  content: string;
  status: 'pending' | 'done' | 'error';
  steps: unknown[];
  created_at: string;
}

export interface CrewThread {
  uid: string;
  title: string;
  agent: string;
  opened_by: string;
  status: 'open' | 'working' | 'done' | 'failed';
  wakes: number;
  created_at: string;
  updated_at: string;
}

export interface CrewMessage {
  uid: string;
  thread: string;
  sender: string;
  recipient: string;
  kind: 'task' | 'question' | 'answer' | 'report' | 'notice' | 'note';
  task: string;
  text: string;
  data: Record<string, unknown>;
  status: string;
  created_at: string;
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
  tier?: string;            // what kind of source (kg/core/sources.py): paper, official, press, blog…
  created_at: string;
  snippet?: string;
  entities?: number;
}

export interface Graph {
  entities: Entity[];
  facts: Fact[];
  total?: { entities: number; facts: number };
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
  tier?: string;            // "paper" when read through the scholarly APIs
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
    sources: { uid: string; source: string; title: string; content_status: string; tier?: string; entities: number }[];
    entities: { uid: string; name: string; type: string }[];
    facts_created: ReportFact[];
    facts_strengthened: ReportFact[];
    facts_invalidated: ReportFact[];
    reviews_decided: { uid: string; kind: string; status: string; summary: string; resolution: string }[];
    research: { id: string; mode: ResearchMode; question: string; status: string; error: string }[];
  };
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

  /** The most connected `limit` entities (0 = all) plus `include` — what the graph view draws. */
  graph: (id: string, limit = 0, include: string[] = []) =>
    request<Graph>(KG_API, `/graph/${encodeURIComponent(id)}?limit=${limit}${include.length ? `&include=${include.map(encodeURIComponent).join(',')}` : ''}`),

  facts: (id: string, uids: string[]) =>
    request<{ facts: Fact[] }>(KG_API, `${d(id)}/facts?uids=${uids.map(encodeURIComponent).join(',')}`).then(r => r.facts),

  activity: (id: string, after = 0) => request<Activity>(KG_API, `${d(id)}/activity?after=${after}`),

  ingest: (id: string, raw_text: string, source: string, title?: string, content_status: 'full' | 'partial' = 'full') =>
    request<IngestResult>(KG_API, `${d(id)}/ingest`, json({ raw_text, source, title, content_status })),

  entity: (id: string, uid: string) => request<EntityDetail>(KG_API, `${d(id)}/entities/${encodeURIComponent(uid)}`),

  /** A page of the review queue and how many it holds in all. */
  reviews: (id: string, status: 'pending' | 'approved' | 'rejected' = 'pending', limit = 100, offset = 0,
            order: 'oldest' | 'newest' = 'oldest') =>
    request<{ reviews: Review[]; total: number }>(
      KG_API, `${d(id)}/reviews?status=${status}&limit=${limit}&offset=${offset}&order=${order}`),

  /** The Head decides the pending queue in the background → {started} (202), or {busy} when it already does. */
  headReview: (id: string, minutes = 10) =>
    request<{ started?: boolean; busy?: boolean }>(KG_API, `${d(id)}/head-review?minutes=${minutes}&wait=false`, { method: 'POST' }),

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

  team: (id: string) => request<{ agents: TeamAgent[]; unread: number }>(KG_API, `${d(id)}/team`),

  threads: (id: string, agent?: string) =>
    request<{ threads: CrewThread[] }>(KG_API, `${d(id)}/threads${agent ? `?agent=${encodeURIComponent(agent)}` : ''}`)
      .then(r => r.threads),

  thread: (id: string, uid: string) =>
    request<CrewThread & { messages: CrewMessage[] }>(KG_API, `${d(id)}/threads/${encodeURIComponent(uid)}`),

  learning: (id: string) =>
    request<{ settings: LearningSettings; last: LearningCycle | null; due: string | null }>(KG_API, `${d(id)}/learning`),

  saveLearning: (id: string, changes: Partial<LearningSettings>) =>
    request<{ settings: LearningSettings }>(KG_API, `${d(id)}/learning`, { method: 'PUT', body: JSON.stringify(changes) })
      .then(r => r.settings),

  learningGaps: (id: string) => request<LearningGaps>(KG_API, `${d(id)}/learning/gaps`),

  charter: (id: string) => request<CharterState & { brief: string }>(KG_API, `${d(id)}/charter`),

  exam: (id: string) => request<ExamState>(KG_API, `${d(id)}/exam`),

  synthesis: (id: string) => request<{ pages: SynthesisPage[]; running: boolean }>(KG_API, `${d(id)}/synthesis`),

  /** Rewrite the review's pages whose facts changed (all with `force`), in the background. */
  writeSynthesis: (id: string, force = false) =>
    request<{ started?: boolean; busy?: boolean; skipped?: string }>(KG_API, `${d(id)}/synthesis`, json({ force })),

  /** Take the exam now, in the background (minutes): {started} — or {busy} / {skipped} without a charter. `fresh`: new
   * questions from the same charter first (the score starts a new series). */
  /** Drop a question that is off the mark: a new version of the exam without it. */
  removeExamQuestion: (id: string, qid: string) =>
    request<unknown>(KG_API, `${d(id)}/exam/questions/${encodeURIComponent(qid)}`, { method: 'DELETE' }),

  takeExam: (id: string, fresh = false) =>
    request<{ started?: boolean; busy?: boolean; skipped?: string }>(KG_API, `${d(id)}/exam`, json({ wait: false, fresh })),

  saveCharter: (id: string, changes: Partial<Charter>) =>
    request<CharterState>(KG_API, `${d(id)}/charter`, { method: 'PUT', body: JSON.stringify(changes) }),

  /** Write it from notes (none: from the description) — a minute with a local model. */
  draftCharter: (id: string, notes = '') => request<CharterState>(KG_API, `${d(id)}/charter/draft`, json({ notes })),

  /** The user agrees: it steers research from now on, and the first time the first deep research starts. */
  confirmCharter: (id: string) => request<CharterState>(KG_API, `${d(id)}/charter/confirm`, { method: 'POST' }),

  decideCharterSuggestion: (id: string, uid: string, accept: boolean) =>
    request<CharterState>(KG_API, `${d(id)}/charter/suggestions/${encodeURIComponent(uid)}`, json({ accept })),

  resetCharter: (id: string) => request<CharterState>(KG_API, `${d(id)}/charter`, { method: 'DELETE' }),

  /** Your conversation with the Head, kept in the KG (Hermes writes each question and answer as it happens). */
  chat: (id: string) => request<{ turns: ChatTurn[] }>(KG_API, `${d(id)}/chat`).then(r => r.turns),

  addChat: (id: string, turn: { role: 'user' | 'assistant'; content: string; status?: ChatTurn['status']; steps?: unknown[] }) =>
    request<ChatTurn>(KG_API, `${d(id)}/chat`, json(turn)),

  clearChat: (id: string) => request<unknown>(KG_API, `${d(id)}/chat`, { method: 'DELETE' }),

  importChat: (id: string, turns: unknown[]) =>
    request<{ imported: number }>(KG_API, `${d(id)}/chat/import`, json({ turns })),

};

// ── Research (DeerFlow) ─────────────────────────────────────────────────────

export const research = {
  health: () => request<{ research: boolean; config?: boolean; last_failed?: string | null; model: string; searxng: boolean; crawler: boolean; schedule: string; tracing: boolean }>(
    RESEARCH_API, '/health'),

  /** A learning cycle now: what's new, the graph's open questions, then keep, forget and brief. */
  cycle: (id: string) => request<{ started: string }>(RESEARCH_API, `${d(id)}/cycle`, { method: 'POST' }),

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

  /** Stop this person's running Head turn (closing the stream alone doesn't: an answer must survive a reload). */
  stopAsk: (id: string) => request<{ stopped: number }>(HERMES_API, `${d(id)}/ask/stop`, { method: 'POST' }),

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
