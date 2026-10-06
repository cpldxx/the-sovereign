import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Bell, Check, ChevronDown, ChevronRight, FileText, Globe, Loader2, Plus, RefreshCw, Send, Trash2, X,
} from 'lucide-react';
import { kg, type ActionDef, type ActionParam, type Fact, type Playbook, type Proposal } from '../lib/api';
import { Markdown } from '../components/Markdown';
import { SensorsView } from '../components/SensorsView';

type View = 'inbox' | 'playbooks' | 'sensors' | 'catalog';

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
          <span className="font-mono text-[10px] text-gold">{f.weight.toFixed(2)}</span>{' '}
          <span className={f.valid ? 'text-dim' : 'text-faint line-through'}>{f.fact}</span>{' '}
          <button onClick={() => onSelect(f.source_uid)} className="text-[10px] text-faint hover:text-ink">{f.source_name} ↗</button>
        </li>
      ))}
    </ul>
  );
}

function PendingCard({ p, facts, onSelect, onDecide }: {
  p: Proposal;
  facts: Map<string, Fact>;
  onSelect: (uid: string) => void;
  onDecide: (uid: string, approve: boolean, note: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  async function decide(approve: boolean) {
    setBusy(true);
    try { await onDecide(p.uid, approve, note); } finally { setBusy(false); }
  }
  return (
    <li className="space-y-2 rounded-md border border-gold/40 bg-gold/5 p-3">
      <div className="flex items-center gap-2">
        <Send size={12} className="text-gold" />
        <span className="font-mono text-[12px] text-ink">{p.action}</span>
        <span className="rounded bg-panel-2 px-1.5 text-[9.5px] text-gold">needs your confirmation</span>
        <span className="ml-auto text-[10px] text-faint">{until(p.expires_at)}</span>
      </div>
      <div className="text-[10.5px] text-faint">proposed by {p.source === 'playbook' ? 'a playbook' : p.source === 'head' ? 'the Head Agent' : 'you'} · {ago(p.created_at)}</div>
      <Markdown>{p.rationale || '_No rationale given._'}</Markdown>
      <Evidence uids={p.evidence} facts={facts} onSelect={onSelect} />
      <div>
        <div className="mb-0.5 font-mono text-[10px] tracking-widest text-faint uppercase">Dry run — what will happen</div>
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-bg p-2 font-mono text-[10.5px] text-dim">{p.preview}</pre>
      </div>
      <div className="flex gap-2">
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="Note (optional)"
          className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-2 py-1 text-[11.5px] outline-none focus:border-gold/60" />
        <button onClick={() => decide(false)} disabled={busy}
          className="flex items-center gap-1 rounded-md border border-line-2 px-2.5 text-[11.5px] text-dim hover:text-ink disabled:opacity-40">
          <X size={12} /> Reject
        </button>
        <button onClick={() => decide(true)} disabled={busy}
          className="flex items-center gap-1 rounded-md bg-gold px-2.5 text-[11.5px] text-black disabled:opacity-40">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} Confirm
        </button>
      </div>
    </li>
  );
}

function ActivityRow({ p, facts, onSelect }: { p: Proposal; facts: Map<string, Fact>; onSelect: (uid: string) => void }) {
  const [open, setOpen] = useState(false);
  const Icon = ICON[p.action] ?? Send;
  const severity = p.params.severity;
  const tone = p.status === 'failed' ? 'text-bad' : p.action === 'alert' && severity === 'critical' ? 'text-bad'
    : p.action === 'alert' && severity === 'warning' ? 'text-gold' : 'text-dim';
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
  onRetire: (uid: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border border-line bg-panel-2/40 p-3">
      <div className="flex items-start gap-2">
        <button onClick={() => setOpen(o => !o)} className="mt-0.5 text-faint">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button>
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] text-ink">{pb.name}</div>
          <p className="mt-0.5 text-[11.5px] leading-snug text-dim"><span className="text-faint">when </span>{pb.situation}</p>
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
            <button onClick={() => onRetire(pb.uid)} className="ml-auto flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-bad">
              <Trash2 size={10} /> Retire
            </button>
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

function Catalog({ domain, actions, onChanged }: { domain: string; actions: ActionDef[]; onChanged: () => Promise<void> }) {
  const [form, setForm] = useState({ name: '', description: '', url: '', params: '', dry_run: false });
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await kg.addAction(domain, { ...form, params: parseParams(form.params) });
      setForm({ name: '', description: '', url: '', params: '', dry_run: false });
      setAdding(false);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const input = 'w-full rounded-md border border-line-2 bg-bg px-2 py-1 text-[12px] outline-none focus:border-gold/60';
  return (
    <div className="space-y-3">
      <ul className="space-y-1.5">
        {actions.map(a => (
          <li key={a.name} className="rounded-md border border-line px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[12px] text-ink">{a.name}</span>
              <span className={`rounded px-1.5 text-[9.5px] ${a.confirm ? 'bg-gold/15 text-gold' : 'bg-panel-2 text-faint'}`}>
                {a.confirm ? 'needs confirmation' : 'runs at once'}
              </span>
              {a.dry_run && <span className="rounded bg-panel-2 px-1.5 text-[9.5px] text-faint">dry run</span>}
              {a.kind === 'webhook' && (
                <button onClick={async () => { await kg.removeAction(domain, a.name); await onChanged(); }}
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
      {adding ? (
        <form onSubmit={add} className="space-y-2 rounded-md border border-line-2 p-3">
          <p className="text-[11px] text-faint">
            An external action: the URL receives a JSON POST with the parameters (Slack, n8n, Zapier, your own API…).
            Agents can only propose it; it runs when you confirm.
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
          {error && <p className="text-xs text-bad">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setAdding(false)} className="px-2 text-[11.5px] text-dim hover:text-ink">Cancel</button>
            <button type="submit" disabled={!form.name || !form.url} className="rounded-md bg-gold px-3 py-1 text-[11.5px] text-black disabled:opacity-40">Add</button>
          </div>
        </form>
      ) : (
        <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 rounded-md border border-dashed border-line-2 px-3 py-1.5 text-[11.5px] text-dim hover:text-ink">
          <Plus size={12} /> Add an external action (webhook)
        </button>
      )}
    </div>
  );
}

export function ActionsPanel({ domain, version, facts, onSelect, onHighlight, onPending }: {
  domain: string;
  version: number;
  facts: Map<string, Fact>;
  onSelect: (uid: string) => void;
  onHighlight: (uids: Set<string>) => void;
  onPending: (count: number) => void;
}) {
  const [view, setView] = useState<View>('inbox');
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [running, setRunning] = useState(false);
  const [catalog, setCatalog] = useState<ActionDef[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [ps, pb, cat] = await Promise.all([kg.proposals(domain), kg.playbooks(domain), kg.actions(domain)]);
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

  async function decide(uid: string, approve: boolean, note: string) {
    try {
      await kg.decideProposal(domain, uid, approve, note);
    } catch (e) {
      setError((e as Error).message);
    }
    await load();
  }

  const pending = proposals.filter(p => p.status === 'proposed');
  const activity = proposals.filter(p => p.status !== 'proposed');
  const tabs: { id: View; label: string }[] = [
    { id: 'inbox', label: pending.length ? `Inbox (${pending.length})` : 'Inbox' },
    { id: 'playbooks', label: `Playbooks (${playbooks.length})` },
    { id: 'sensors', label: 'Sensors' },
    { id: 'catalog', label: 'Catalog' },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-line p-2">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setView(t.id)}
            className={`rounded-md px-2.5 py-1 text-[11.5px] ${view === t.id ? 'bg-panel-2 text-ink' : 'text-faint hover:text-dim'}`}>
            {t.label}
          </button>
        ))}
        {view === 'playbooks' && (
          <button onClick={async () => { await kg.runPlaybooks(domain).catch(e => setError((e as Error).message)); setRunning(true); }}
            disabled={running} title="Check the playbooks against the last 24 hours, then rewrite them from the graph"
            className="ml-auto flex items-center gap-1 rounded-md border border-line-2 px-2 py-1 text-[11px] text-dim hover:text-ink disabled:opacity-50">
            {running ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} {running ? 'Running…' : 'Run now'}
          </button>
        )}
      </div>
      {error && <p className="flex items-center gap-1 px-3 pt-2 text-xs text-bad"><AlertTriangle size={11} /> {error}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {view === 'inbox' && (
          <div className="space-y-4">
            {pending.length > 0 && (
              <ul className="space-y-2">
                {pending.map(p => <PendingCard key={p.uid} p={p} facts={facts} onSelect={onSelect} onDecide={decide} />)}
              </ul>
            )}
            {pending.length === 0 && (
              <p className="text-[11.5px] leading-snug text-faint">
                Nothing waits for you. External actions proposed by the Head Agent or a playbook appear here with a dry
                run; they run only when you confirm. Alerts and drafts land in the activity below.
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
              Responses prepared at night from the graph. When new knowledge shows a situation happening, its playbook
              proposes the action by itself.
            </p>
            {playbooks.length === 0 && <p className="mt-4 text-center text-xs text-faint">No playbooks yet — they are written after the nightly research, or Run now.</p>}
            <ul className="space-y-2">
              {playbooks.map(pb => (
                <PlaybookCard key={pb.uid} pb={pb} facts={facts} onSelect={onSelect} onHighlight={onHighlight}
                  onRetire={async uid => { await kg.retirePlaybook(domain, uid); await load(); }} />
              ))}
            </ul>
          </div>
        )}
        {view === 'sensors' && <SensorsView domain={domain} version={version} />}
        {view === 'catalog' && <Catalog domain={domain} actions={catalog} onChanged={load} />}
      </div>
    </div>
  );
}
