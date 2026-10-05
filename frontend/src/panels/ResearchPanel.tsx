import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, Globe, Loader2, Sparkles, Telescope } from 'lucide-react';
import { research, type ResearchJob, type ResearchPage } from '../lib/api';
import { Markdown } from '../components/Markdown';

const ACTIVE = new Set(['queued', 'researching', 'ontology', 'ingesting']);

const STATUS_LABEL: Record<ResearchJob['status'], string> = {
  queued: 'queued',
  researching: 'reading the web',
  ontology: 'deriving ontology',
  ingesting: 'adding to graph',
  done: 'done',
  failed: 'failed',
};

const MODE_LABEL = { bootstrap: 'bootstrap', update: "what's new", mission: 'mission' } as const;

function PageRow({ page }: { page: ResearchPage }) {
  const ing = page.ingest;
  const outcome = page.status === 'failed'
    ? page.error
    : !ing ? 'not ingested'
    : ing.error ? `ingest failed: ${ing.error}`
    : ing.skipped ? ing.skipped
    : ing.duplicate ? 'duplicate'
    : `+${ing.entities_created ?? 0} entities · +${ing.facts_created ?? 0} facts`
      + (ing.facts_strengthened ? ` · ${ing.facts_strengthened} strengthened` : '')
      + (ing.facts_invalidated ? ` · ${ing.facts_invalidated} superseded` : '')
      + (ing.review_items ? ` · ${ing.review_items} to review` : '');
  return (
    <li className="py-1.5 text-[11.5px]">
      <a href={page.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-dim hover:text-ink">
        <ExternalLink size={10} className="shrink-0" />
        <span className="truncate">{page.title || page.url}</span>
        {page.status === 'partial' && <span className="shrink-0 text-[9.5px] text-gold">partial</span>}
      </a>
      <div className={`pl-3.5 text-[10.5px] ${page.status === 'failed' || ing?.error ? 'text-bad' : 'text-faint'}`}>{outcome}</div>
    </li>
  );
}

function JobCard({ job, open, onToggle }: { job: ResearchJob; open: boolean; onToggle: () => void }) {
  const [detail, setDetail] = useState<ResearchJob | null>(null);
  const active = ACTIVE.has(job.status);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const load = () => research.job(job.id).then(j => { if (alive) setDetail(j); }).catch(() => {});
    void load();
    if (!active) return () => { alive = false; };
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [open, job.id, active, job.status]);

  const j = detail ?? job;
  const s = j.summary ?? {};
  return (
    <li className="rounded-md border border-line bg-panel-2/40">
      <button onClick={onToggle} className="flex w-full items-start gap-2 p-3 text-left">
        {open ? <ChevronDown size={13} className="mt-0.5 shrink-0 text-faint" /> : <ChevronRight size={13} className="mt-0.5 shrink-0 text-faint" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="rounded bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-dim">{MODE_LABEL[j.mode]}</span>
            <span className={`flex items-center gap-1 text-[11px] ${j.status === 'failed' ? 'text-bad' : active ? 'text-gold' : 'text-good'}`}>
              {active && <Loader2 size={11} className="animate-spin" />}{STATUS_LABEL[j.status]}
            </span>
            <span className="ml-auto font-mono text-[10px] text-faint">{j.created_at.slice(5, 16).replace('T', ' ')}</span>
          </div>
          {j.question && <p className="mt-1 truncate text-[12px] text-ink">{j.question}</p>}
          {j.status === 'done' && (
            <p className="mt-1 text-[11px] text-dim">
              {s.pages_read ?? 0} pages read · +{s.entities_created ?? 0} entities · +{s.facts_created ?? 0} facts
              {s.facts_strengthened ? ` · ${s.facts_strengthened} strengthened` : ''}
              {s.facts_invalidated ? ` · ${s.facts_invalidated} superseded` : ''}
              {s.pages_failed ? ` · ${s.pages_failed} unreadable` : ''}
            </p>
          )}
          {active && j.steps.length > 0 && <p className="mt-1 truncate font-mono text-[10.5px] text-faint">{j.steps[j.steps.length - 1].text}</p>}
          {j.error && <p className="mt-1 flex items-center gap-1 text-[11px] text-bad"><AlertTriangle size={11} /> {j.error.slice(0, 160)}</p>}
        </div>
      </button>
      {open && detail && (
        <div className="space-y-3 border-t border-line px-3 pb-3 pt-2">
          {detail.pages && detail.pages.length > 0 && (
            <section>
              <h4 className="font-mono text-[10px] tracking-widest text-faint uppercase">Pages ({detail.pages.length})</h4>
              <ul className="divide-y divide-line">{detail.pages.map(p => <PageRow key={p.url} page={p} />)}</ul>
            </section>
          )}
          {detail.report && (
            <section>
              <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Report (reference only — not in the graph)</h4>
              <Markdown>{detail.report}</Markdown>
            </section>
          )}
          <details>
            <summary className="cursor-pointer font-mono text-[10px] tracking-widest text-faint uppercase">Steps ({detail.steps.length})</summary>
            <ul className="mt-1 max-h-48 overflow-y-auto font-mono text-[10.5px] text-faint">
              {detail.steps.map((st, i) => <li key={i} className="truncate">{st.text}</li>)}
            </ul>
          </details>
        </div>
      )}
    </li>
  );
}

export function ResearchPanel({ domain, onIngesting }: { domain: string; onIngesting: () => void }) {
  const [jobs, setJobs] = useState<ResearchJob[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await research.jobs(domain);
      setJobs(list);
      setError(null);
      if (list.some(j => j.status === 'ingesting')) onIngesting();
    } catch (e) {
      setError((e as Error).message);
      setJobs(j => j ?? []);
    }
  }, [domain, onIngesting]);

  useEffect(() => { void load(); }, [load]);
  const anyActive = jobs?.some(j => ACTIVE.has(j.status));
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [anyActive, load]);

  async function start(mode: 'bootstrap' | 'update' | 'mission') {
    setBusy(true); setError(null);
    try {
      const job = await research.start(domain, mode, mode === 'mission' ? question.trim() : '');
      if (mode === 'mission') setQuestion('');
      setOpen(job.id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const hasBootstrap = jobs?.some(j => j.mode === 'bootstrap' && j.status !== 'failed');

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2.5 border-b border-line p-3">
        <p className="text-[11px] leading-snug text-faint">
          The research agent (DeerFlow) searches and reads the web; every page it reads becomes a source in the
          graph. It runs every night by itself — or start a run now.
        </p>
        <div className="flex gap-2">
          <button
            onClick={() => start('bootstrap')}
            disabled={busy}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11.5px] disabled:opacity-40 ${hasBootstrap ? 'border-line-2 text-dim hover:text-ink' : 'border-gold/60 text-gold hover:bg-gold/10'}`}
            title="Broad first research: map the domain and derive its ontology from real sources"
          >
            <Telescope size={12} /> Bootstrap
          </button>
          <button
            onClick={() => start('update')}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1.5 text-[11.5px] text-dim hover:text-ink disabled:opacity-40"
            title="Find what is new since the last runs"
          >
            <Sparkles size={12} /> What's new
          </button>
        </div>
        <form onSubmit={e => { e.preventDefault(); if (question.trim()) void start('mission'); }} className="flex gap-2">
          <input
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder="Research mission: e.g. How exposed is the supply chain to export controls?"
            className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-3 py-1.5 text-[12.5px] outline-none focus:border-gold/60"
          />
          <button type="submit" disabled={busy || !question.trim()} className="rounded-md bg-gold px-3 text-black disabled:opacity-40" title="Start mission">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />}
          </button>
        </form>
        {error && <p className="text-xs text-bad">{error}</p>}
      </div>

      <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {jobs && jobs.length === 0 && <p className="mt-6 text-center text-xs text-faint">No research yet — start with Bootstrap.</p>}
        {jobs?.map(j => (
          <JobCard key={j.id} job={j} open={open === j.id} onToggle={() => setOpen(o => (o === j.id ? null : j.id))} />
        ))}
      </ul>
    </div>
  );
}
