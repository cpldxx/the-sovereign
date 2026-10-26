import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Bell, Check, ChevronDown, ChevronRight, FileText, Globe, Loader2, Plus, RefreshCw, Send, Trash2, X,
} from 'lucide-react';
import { can, kg, type ActionDef, type ActionParam, type Fact, type Playbook, type Proposal, type Role } from '../lib/api';
import { Markdown } from '../components/Markdown';
import { SensorsView } from '../components/SensorsView';

export type ActionsView = 'inbox' | 'playbooks' | 'sensors' | 'catalog';
type View = ActionsView;

function ago(iso: string) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return min < 60 ? `${min}m ago` : min < 48 * 60 ? `${Math.round(min / 60)}h ago` : `${Math.round(min / 1440)}d ago`;
}

function until(iso: string) {
  const h = (new Date(iso).getTime() - Date.now()) / 3600000;
  return h <= 0 ? 'expired' : h < 1 ? `expires in ${Math.round(h * 60)}m` : `expires in ${Math.round(h)}h`;
}

const ICON = { alert: Bell, draft: FileText, research: Globe } as Record<string, typeof Bell>;

function title(p: Proposal) {
  const t = p.params.title ?? p.params.question ?? p.params.message;
  return typeof t === 'string' ? t : p.action;
}

function Evidence({ uids, facts, onSelect }: { uids: string[]; facts: Map<string, Fact>; onSelect: (uid: string) => void }) {
  const known = uids.map(u => facts.get(u)).filter((f): f is Fact => !!f);
  if (!known.length) return null;
  return (
    <ul className="space-y-0.5">
      {known.map(f => (
        <li key={f.uid} className="text-[11px] leading-snug">
          <span className="font-mono text-[10px] text-accent">{f.weight.toFixed(2)}</span>{' '}
          <span className={f.valid ? 'text-dim' : 'text-faint line-through'}>{f.fact}</span>{' '}
          <button onClick={() => onSelect(f.source_uid)} className="text-[10px] text-faint hover:text-ink">{f.source_name} ↗</button>
        </li>
      ))}
    </ul>
  );
}

/** Decisions the KG refused (403: not a person; 409: already decided, or the API changed since the proposal), kept
 * on their card with the server's reason until dismissed — the proposal itself leaves the pending list on the next
 * load, and with it the reason. */
function useDecisions(domain: string, load: () => Promise<void>) {
  const [refused, setRefused] = useState<Map<string, { p: Proposal; why: string }>>(new Map());
  const drop = (uid: string) => setRefused(m => { const n = new Map(m); n.delete(uid); return n; });
  async function decide(p: Proposal, approve: boolean, note: string) {
    try {
      await kg.decideProposal(domain, p.uid, approve, note);
      drop(p.uid);
    } catch (e) {
      const why = (e as Error).message;
      setRefused(m => new Map(m).set(p.uid, { p, why }));
    }
    await load();
  }
  /** The pending proposals, then the refused ones that are no longer pending. */
  const cards = (pending: Proposal[]) =>
    [...pending, ...[...refused.values()].filter(r => !pending.some(p => p.uid === r.p.uid)).map(r => r.p)];
  return { refused, decide, drop, cards };
}

function PendingCard({ p, facts, onSelect, onDecide, editable, refusal, closed, onDismiss }: {
  p: Proposal;
  facts: Map<string, Fact>;
  onSelect: (uid: string) => void;
  onDecide: (p: Proposal, approve: boolean, note: string) => Promise<void>;
  editable: boolean;     // editors and owners decide; a viewer sees what waits
  refusal?: string;      // why the KG refused the last decision
  closed?: boolean;      // no longer pending: only the reason is left to read
  onDismiss: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  async function decide(approve: boolean) {
    setBusy(true);
    try { await onDecide(p, approve, note); } finally { setBusy(false); }
  }
  return (
    <li className="space-y-2 rounded-md border border-accent/40 bg-accent/5 p-3">
      <div className="flex items-center gap-2">
        <Send size={12} className="text-accent" />
        <span className="font-mono text-[12px] text-ink">{p.action}</span>
        <span className="rounded bg-panel-2 px-1.5 text-[9.5px] text-accent">needs your confirmation</span>
        <span className="ml-auto text-[10px] text-faint">{until(p.expires_at)}</span>
      </div>
      <div className="text-[10.5px] text-faint">proposed by {p.source === 'playbook' ? 'a playbook' : p.source === 'head' ? 'the Head Agent' : 'you'} · {ago(p.created_at)}</div>
      <Markdown>{p.rationale || '_No rationale given._'}</Markdown>
      <Evidence uids={p.evidence} facts={facts} onSelect={onSelect} />
      <div>
        <div className="mb-0.5 font-mono text-[10px] tracking-widest text-faint uppercase">Dry run — what will happen</div>
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-bg p-2 font-mono text-[10.5px] text-dim">{p.preview}</pre>
      </div>
      {refusal && (
        <p className="flex items-start gap-1 text-[11.5px] text-bad">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" /> <span className="flex-1">{refusal}</span>
          <button onClick={onDismiss} className="shrink-0 text-faint hover:text-ink" title="Dismiss"><X size={12} /></button>
        </p>
      )}
      {!editable && !closed && <p className="text-[11px] text-faint">An editor or owner of this domain confirms or rejects it.</p>}
      {editable && !closed && <div className="flex gap-2">
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="Note (optional)"
          className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-2 py-1 text-[11.5px] outline-none focus:border-accent/60" />
        <button onClick={() => decide(false)} disabled={busy}
          className="flex items-center gap-1 rounded-md border border-line-2 px-2.5 text-[11.5px] text-dim hover:text-ink disabled:opacity-40">
          <X size={12} /> Reject
        </button>
        <button onClick={() => decide(true)} disabled={busy}
          className="flex items-center gap-1 rounded-md bg-accent px-2.5 text-[11.5px] text-black disabled:opacity-40">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} Confirm
        </button>
      </div>}
    </li>
  );
}

function ActivityRow({ p, facts, onSelect }: { p: Proposal; facts: Map<string, Fact>; onSelect: (uid: string) => void }) {
  const [open, setOpen] = useState(false);
  const Icon = ICON[p.action] ?? Send;
  const severity = p.params.severity;
  const tone = p.status === 'failed' ? 'text-bad' : p.action === 'alert' && severity === 'critical' ? 'text-bad'
    : p.action === 'alert' && severity === 'warning' ? 'text-accent' : 'text-dim';
  const body = p.params.message ?? p.params.body;
  return (
    <li className="border-b border-line py-1.5">
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-2 text-left">
        {open ? <ChevronDown size={11} className="shrink-0 text-faint" /> : <ChevronRight size={11} className="shrink-0 text-faint" />}
        <Icon size={12} className={`shrink-0 ${tone}`} />
        <span className={`truncate text-[12px] ${p.status === 'rejected' || p.status === 'expired' ? 'text-faint line-through' : 'text-ink'}`}>{title(p)}</span>
        <span className="ml-auto shrink-0 font-mono text-[10px] text-faint">{p.status} · {ago(p.created_at)}</span>
      </button>
      {open && (
        <div className="space-y-2 py-2 pl-6">
          {typeof body === 'string' && <Markdown>{body}</Markdown>}
          {p.rationale && <p className="text-[11px] text-faint">{p.rationale}</p>}
          <Evidence uids={p.evidence} facts={facts} onSelect={onSelect} />
          {p.result && <pre className="whitespace-pre-wrap font-mono text-[10.5px] text-faint">{JSON.stringify(p.result, null, 1)}</pre>}
          {p.note && <p className="text-[11px] text-faint">note: {p.note}</p>}
        </div>
      )}
    </li>
  );
}

function PlaybookCard({ pb, facts, onSelect, onHighlight, onRetire }: {
  pb: Playbook;
  facts: Map<string, Fact>;
  onSelect: (uid: string) => void;
  onHighlight: (uids: Set<string>) => void;
  onRetire?: (uid: string) => void;   // editors only
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border border-line bg-panel-2/40 p-3">
      <div className="flex items-start gap-2">
        <button onClick={() => setOpen(o => !o)} className="mt-0.5 text-faint">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button>
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] text-ink">{pb.name}</div>
          <p className="mt-0.5 text-[11.5px] leading-snug text-dim"><span className="text-faint">when </span>{pb.situation}</p>
          {pb.trigger && (
            <p className="mt-0.5 font-mono text-[10.5px] text-accent" title="Checked around the clock; fires the playbook when it holds">
              live: {pb.trigger.sensor}({Object.entries(pb.trigger.params).map(([k, v]) => `${k}=${v}`).join(', ')}).{pb.trigger.field} {pb.trigger.op} {pb.trigger.value}
              <span className="text-faint"> · every {pb.trigger.every_minutes}m{pb.trigger_value != null ? ` · now ${pb.trigger_value}` : ''}</span>
            </p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {pb.watch.map(w => (
              <button key={w.uid} onClick={() => onSelect(w.uid)} className="rounded bg-panel-2 px-1.5 py-0.5 text-[10.5px] text-dim hover:text-ink">{w.name}</button>
            ))}
            <span className="ml-auto font-mono text-[10px] text-faint">→ {pb.action}{pb.fired ? ` · fired ${pb.fired}×` : ''}</span>
          </div>
        </div>
      </div>
      {open && (
        <div className="mt-2 space-y-2 border-t border-line pt-2 pl-5">
          <p className="text-[11.5px] leading-snug text-dim"><span className="text-faint">response </span>{pb.response}</p>
          <Evidence uids={pb.evidence} facts={facts} onSelect={onSelect} />
          <div className="flex gap-2">
            <button onClick={() => onHighlight(new Set(pb.watch.map(w => w.uid)))} className="rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-ink">Highlight watched</button>
            {onRetire && (
              <button onClick={() => onRetire(pb.uid)} className="ml-auto flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-bad">
                <Trash2 size={10} /> Retire
              </button>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

function parseParams(text: string): ActionParam[] {
  return text.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
    const [name, type = 'string', ...rest] = l.split(':').map(x => x.trim());
    return { name, type: type as ActionParam['type'], description: rest.join(':'), required: true };
  });
}

function Catalog({ domain, actions, owner, onChanged }: {
  domain: string;
  actions: ActionDef[];
  owner: boolean;   // connecting and removing APIs is the owner's
  onChanged: () => Promise<void>;
}) {
  const [form, setForm] = useState({ name: '', description: '', url: '', params: '', dry_run: false, auto: false });
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await kg.addAction(domain, { ...form, params: parseParams(form.params) });
      setForm({ name: '', description: '', url: '', params: '', dry_run: false, auto: false });
      setAdding(false);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const input = 'w-full rounded-md border border-line-2 bg-bg px-2 py-1 text-[12px] outline-none focus:border-accent/60';
  return (
    <div className="space-y-3">
      <ul className="space-y-1.5">
        {actions.map(a => (
          <li key={a.name} className="rounded-md border border-line px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[12px] text-ink">{a.name}</span>
              <span className={`rounded px-1.5 text-[9.5px] ${a.confirm ? 'bg-accent/15 text-accent' : 'bg-panel-2 text-faint'}`}>
                {a.confirm ? 'asks you first' : a.auto ? 'runs when the Head says' : 'runs at once'}
              </span>
              {a.dry_run && <span className="rounded bg-panel-2 px-1.5 text-[9.5px] text-faint">dry run</span>}
              {a.kind === 'webhook' && owner && (
                <button onClick={async () => {
                  setError(null);
                  try { await kg.removeAction(domain, a.name); await onChanged(); } catch (err) { setError((err as Error).message); }
                }}
                  className="ml-auto text-faint hover:text-bad" title="Remove"><Trash2 size={12} /></button>
              )}
            </div>
            <p className="mt-0.5 text-[11px] text-dim">{a.description}</p>
            {a.url && <p className="truncate font-mono text-[10px] text-faint">{a.url}</p>}
            {a.params.length > 0 && (
              <p className="font-mono text-[10px] text-faint">{a.params.map(p => `${p.name}: ${p.type}`).join(' · ')}</p>
            )}
          </li>
        ))}
      </ul>
      {!adding && error && <p className="text-xs text-bad">{error}</p>}
      {!owner ? (
        <p className="text-[11px] text-faint">The domain's owner connects APIs.</p>
      ) : adding ? (
        <form onSubmit={add} className="space-y-2 rounded-md border border-line-2 p-3">
          <p className="text-[11px] text-faint">
            Connect an API: the URL receives a JSON POST with the parameters and answers with a result (a simulation,
            Slack, n8n, your own API…). Only the Head decides to use it — its Operator runs it and reports what came
            back.
          </p>
          <input className={input} placeholder="name, e.g. notify_slack" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input className={input} placeholder="What it does (agents read this)" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
          <input className={input} placeholder="https://hooks.example.com/…" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} />
          <textarea className={`${input} h-16 font-mono`} placeholder={'one parameter per line — name:type:description\nmessage:string:Text to post'}
            value={form.params} onChange={e => setForm({ ...form, params: e.target.value })} />
          <label className="flex items-center gap-2 text-[11px] text-dim">
            <input type="checkbox" checked={form.dry_run} onChange={e => setForm({ ...form, dry_run: e.target.checked })} />
            The endpoint understands <code className="font-mono">dry_run: true</code> (its answer becomes the preview)
          </label>
          <label className="flex items-start gap-2 text-[11px] text-dim">
            <input type="checkbox" className="mt-0.5" checked={form.auto} onChange={e => setForm({ ...form, auto: e.target.checked })} />
            <span>Runs when the Head says so, without asking you — only for actions with no real-world effect
              (a simulation, a calculation). Otherwise each run waits for your confirmation here.</span>
          </label>
          {error && <p className="text-xs text-bad">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setAdding(false)} className="px-2 text-[11.5px] text-dim hover:text-ink">Cancel</button>
            <button type="submit" disabled={!form.name || !form.url} className="rounded-md bg-accent px-3 py-1 text-[11.5px] text-black disabled:opacity-40">Add</button>
          </div>
        </form>
      ) : (
        <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 rounded-md border border-dashed border-line-2 px-3 py-1.5 text-[11.5px] text-dim hover:text-ink">
          <Plus size={12} /> Connect an API (webhook)
        </button>
      )}
    </div>
  );
}

/** What waits for your confirmation — on top of the Head chat, where the decision to act was made. */
export function PendingActions({ domain, role, version, onSelect, onPending }: {
  domain: string;
  role: Role;
  version: number;
  onSelect: (uid: string) => void;
  onPending: (count: number) => void;
}) {
  const [pending, setPending] = useState<Proposal[]>([]);
  const [facts, setFacts] = useState<Map<string, Fact>>(new Map());
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const ps = await kg.proposals(domain, 'proposed');
      const uids = [...new Set(ps.flatMap(p => p.evidence ?? []))];
      if (uids.length) {
        const found = await kg.facts(domain, uids.slice(0, 200)).catch(() => []);
        setFacts(new Map(found.map(f => [f.uid, f])));
      }
      setPending(ps);
      onPending(ps.length);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain, onPending]);
  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => {
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  const { refused, decide, drop, cards } = useDecisions(domain, load);
  const shown = cards(pending);

  if (!shown.length && !error) return null;
  return (
    <div className="max-h-[45%] shrink-0 overflow-y-auto border-b border-line p-3">
      <h4 className="mb-2 font-mono text-[10px] tracking-widest text-accent uppercase">Waiting for your confirmation · {pending.length}</h4>
      {error && <p className="mb-2 text-xs text-bad">{error}</p>}
      <ul className="space-y-2">
        {shown.map(p => (
          <PendingCard key={p.uid} p={p} facts={facts} onSelect={onSelect} onDecide={decide} editable={can(role, 'editor')}
            refusal={refused.get(p.uid)?.why} closed={!pending.includes(p)} onDismiss={() => drop(p.uid)} />
        ))}
      </ul>
    </div>
  );
}

/** The actions' views; with `view`, just that one (the Briefing's activity, the Settings' playbooks / sensors / APIs). */
export function ActionsPanel({ domain, role, version, onSelect, onHighlight, onPending, view: only }: {
  domain: string;
  role: Role;
  version: number;
  onSelect: (uid: string) => void;
  onHighlight: (uids: Set<string>) => void;
  onPending: (count: number) => void;
  view?: View;
}) {
  const [picked, setView] = useState<View>('inbox');
  const view = only ?? picked;
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [running, setRunning] = useState(false);
  const [catalog, setCatalog] = useState<ActionDef[]>([]);
  const [error, setError] = useState<string | null>(null);
  // The facts behind proposals and playbooks, fetched by uid (the app no longer loads the whole graph).
  const [facts, setFacts] = useState<Map<string, Fact>>(new Map());

  const load = useCallback(async () => {
    try {
      const [ps, pb, cat] = await Promise.all([kg.proposals(domain), kg.playbooks(domain), kg.actions(domain)]);
      const uids = [...new Set([...ps.flatMap(p => p.evidence ?? []), ...pb.playbooks.flatMap(b => b.evidence ?? [])])];
      if (uids.length) {
        const found = await kg.facts(domain, uids.slice(0, 200)).catch(() => []);
        setFacts(new Map(found.map(f => [f.uid, f])));
      }
      setProposals(ps);
      setPlaybooks(pb.playbooks);
      setRunning(pb.running);
      setCatalog(cat);
      onPending(ps.filter(p => p.status === 'proposed').length);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain, onPending]);

  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => {
    const t = setInterval(load, running ? 5000 : 15000);
    return () => clearInterval(t);
  }, [load, running]);

  const { refused, decide, drop, cards } = useDecisions(domain, load);
  const editable = can(role, 'editor');
  const pending = proposals.filter(p => p.status === 'proposed');
  const shown = cards(pending);
  const activity = proposals.filter(p => p.status !== 'proposed');
  const tabs: { id: View; label: string }[] = [
    { id: 'inbox', label: pending.length ? `Inbox (${pending.length})` : 'Inbox' },
    { id: 'playbooks', label: `Playbooks (${playbooks.length})` },
    { id: 'sensors', label: 'Sensors' },
    { id: 'catalog', label: 'Catalog' },
  ];

  return (
    <div className="flex h-full flex-col">
      {(!only || view === 'playbooks') && <div className="flex items-center gap-1 border-b border-line p-2">
        {!only && tabs.map(t => (
          <button key={t.id} onClick={() => setView(t.id)}
            className={`rounded-md px-2.5 py-1 text-[11.5px] ${view === t.id ? 'bg-panel-2 text-ink' : 'text-faint hover:text-dim'}`}>
            {t.label}
          </button>
        ))}
        {view === 'playbooks' && editable && (
          <button onClick={async () => {
              try { await kg.runPlaybooks(domain); setRunning(true); } catch (e) { setError((e as Error).message); }
            }}
            disabled={running} title="Check the playbooks against the last 24 hours, then rewrite them from the graph"
            className="ml-auto flex items-center gap-1 rounded-md border border-line-2 px-2 py-1 text-[11px] text-dim hover:text-ink disabled:opacity-50">
            {running ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} {running ? 'Running…' : 'Run now'}
          </button>
        )}
      </div>}
      {error && <p className="flex items-center gap-1 px-3 pt-2 text-xs text-bad"><AlertTriangle size={11} /> {error}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {view === 'inbox' && (
          <div className="space-y-4">
            {shown.length > 0 && (
              <ul className="space-y-2">
                {shown.map(p => (
                  <PendingCard key={p.uid} p={p} facts={facts} onSelect={onSelect} onDecide={decide} editable={editable}
                    refusal={refused.get(p.uid)?.why} closed={!pending.includes(p)} onDismiss={() => drop(p.uid)} />
                ))}
              </ul>
            )}
            {shown.length === 0 && (
              <p className="text-[11.5px] leading-snug text-faint">
                Nothing waits for you. When the Head decides to use an API that asks you first, it appears here with a
                dry run and runs only when you confirm; the Operator then reports the result to the Head. Alerts,
                drafts and auto runs land in the activity below.
              </p>
            )}
            {activity.length > 0 && (
              <section>
                <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Activity</h4>
                <ul>{activity.map(p => <ActivityRow key={p.uid} p={p} facts={facts} onSelect={onSelect} />)}</ul>
              </section>
            )}
          </div>
        )}
        {view === 'playbooks' && (
          <div className="space-y-2">
            <p className="text-[11px] leading-snug text-faint">
              Responses prepared at night from the graph. When new knowledge or a live reading shows a situation
              happening, the Watcher reports the playbook to the Head, which decides whether its Operator acts.
            </p>
            {playbooks.length === 0 && <p className="mt-4 text-center text-xs text-faint">No playbooks yet — they are written after the nightly research, or Run now.</p>}
            <ul className="space-y-2">
              {playbooks.map(pb => (
                <PlaybookCard key={pb.uid} pb={pb} facts={facts} onSelect={onSelect} onHighlight={onHighlight}
                  onRetire={editable ? async uid => {
                    try { await kg.retirePlaybook(domain, uid); await load(); } catch (e) { setError((e as Error).message); }
                  } : undefined} />
              ))}
            </ul>
          </div>
        )}
        {view === 'sensors' && <SensorsView domain={domain} version={version} editable={editable} />}
        {view === 'catalog' && <Catalog domain={domain} actions={catalog} owner={can(role, 'owner')} onChanged={load} />}
      </div>
    </div>
  );
}
