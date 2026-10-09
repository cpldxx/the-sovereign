import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Bot, Loader2, Users } from 'lucide-react';
import { kg, type CrewMessage, type CrewThread, type TeamAgent } from '../lib/api';
import { Markdown } from '../components/Markdown';

const KIND_LABEL: Record<CrewMessage['kind'], string> = {
  task: 'task', question: 'question', answer: 'answer', report: 'report', notice: 'notice', note: 'decision',
};

const STATUS_STYLE: Record<CrewThread['status'], string> = {
  open: 'text-dim', working: 'text-gold', done: 'text-good', failed: 'text-bad',
};

function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago`
    : `${Math.round(s / 86400)}d ago`;
}

/** The Head and its agents at a glance: who is working, and every conversation between them. */
function Roster({ agents, filter, onFilter }: { agents: TeamAgent[]; filter: string | null; onFilter: (a: string | null) => void }) {
  return (
    <div className="space-y-2 border-b border-line p-3">
      <div className="flex justify-center">
        <button
          onClick={() => onFilter(null)}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1 text-[12px] ${filter === null ? 'border-gold/60 text-gold' : 'border-line-2 text-dim hover:text-ink'}`}
          title="All conversations"
        >
          <Bot size={12} /> Head
        </button>
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        {agents.map(a => (
          <button
            key={a.name}
            onClick={() => onFilter(filter === a.name ? null : a.name)}
            title={`${a.role}${a.tasks.length ? ` — tasks: ${a.tasks.map(t => t.name).join(', ')}` : ' — questions only'}`}
            className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-left text-[11.5px] ${filter === a.name ? 'border-gold/60 text-ink' : 'border-line-2 text-dim hover:text-ink'}`}
          >
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${a.state === 'working' ? 'animate-pulse bg-gold' : a.state === 'queued' ? 'bg-gold/50' : 'bg-line-2'}`} />
            <span className="truncate">{a.title}</span>
            {a.queued > 0 && <span className="ml-auto font-mono text-[10px] text-faint">{a.queued}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

function Conversation({ domain, uid, onBack }: { domain: string; uid: string; onBack: () => void }) {
  const [thread, setThread] = useState<(CrewThread & { messages: CrewMessage[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setThread(await kg.thread(domain, uid)); setError(null); } catch (e) { setError((e as Error).message); }
  }, [domain, uid]);
  useEffect(() => { void load(); }, [load]);
  const live = thread && (thread.status === 'working' || thread.status === 'open');
  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [live, load]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <button onClick={onBack} className="text-dim hover:text-ink" title="Back"><ArrowLeft size={14} /></button>
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink">{thread?.title ?? '…'}</span>
        {thread && <span className={`text-[11px] ${STATUS_STYLE[thread.status]}`}>{thread.status}</span>}
      </div>
      {error && <p className="p-3 text-xs text-bad">{error}</p>}
      <ul className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3">
        {thread?.messages.map(m => {
          const head = m.sender === 'head';
          return (
            <li key={m.uid} className={`flex ${head ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[88%] rounded-lg border px-2.5 py-1.5 ${head ? 'border-gold/40 bg-gold/5' : 'border-line-2 bg-panel'}`}>
                <div className="mb-0.5 flex items-center gap-1.5 text-[10.5px] text-faint">
                  <span className={head ? 'text-gold' : 'text-dim'}>{head ? 'Head' : m.sender}</span>
                  <span>→ {m.recipient === 'head' ? 'Head' : m.recipient}</span>
                  <span className="rounded bg-line px-1">{KIND_LABEL[m.kind] ?? m.kind}{m.task ? ` · ${m.task}` : ''}</span>
                  <span className="ml-auto">{ago(m.created_at)}</span>
                </div>
                <div className="text-[12px] leading-snug text-ink"><Markdown>{m.text}</Markdown></div>
                {(m.status === 'queued' || m.status === 'working' || m.status === 'waiting') && (
                  <p className="mt-1 flex items-center gap-1 text-[10.5px] text-gold">
                    <Loader2 size={10} className="animate-spin" /> {m.status === 'queued' ? 'in the queue' : 'working on it'}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function TeamPanel({ domain }: { domain: string }) {
  const [agents, setAgents] = useState<TeamAgent[]>([]);
  const [threads, setThreads] = useState<CrewThread[] | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [team, list] = await Promise.all([kg.team(domain), kg.threads(domain, filter ?? undefined)]);
      setAgents(team.agents);
      setThreads(list);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setThreads(t => t ?? []);
    }
  }, [domain, filter]);
  useEffect(() => { void load(); }, [load]);
  const busy = agents.some(a => a.state !== 'idle');
  useEffect(() => {
    const t = setInterval(load, busy ? 4000 : 15000);
    return () => clearInterval(t);
  }, [busy, load]);

  if (open) return <Conversation domain={domain} uid={open} onBack={() => { setOpen(null); void load(); }} />;

  return (
    <div className="flex h-full flex-col">
      <Roster agents={agents} filter={filter} onFilter={setFilter} />
      {error && <p className="px-3 pt-2 text-xs text-bad">{error}</p>}
      <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto">
        {threads && threads.length === 0 && (
          <p className="mt-6 px-6 text-center text-xs leading-relaxed text-faint">
            <Users size={14} className="mx-auto mb-1.5" />
            No conversations yet. Ask the Head for something its team can do — research a question, find a live
            data source, check a claim — and you'll see the Head and its agents talk here.
          </p>
        )}
        {threads?.map(t => (
          <li key={t.uid}>
            <button onClick={() => setOpen(t.uid)} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-panel">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12px] text-ink">{t.title}</span>
                <span className="text-[10.5px] text-faint">{t.agent} · {ago(t.updated_at)}{t.opened_by !== 'head' ? ` · from ${t.opened_by}` : ''}</span>
              </span>
              <span className={`text-[11px] ${STATUS_STYLE[t.status]}`}>{t.status}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
