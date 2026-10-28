import { useCallback, useEffect, useState } from 'react';
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react';
import { kg, type ExamCore, type SynthesisPage } from '../lib/api';
import { TierChip } from '../components/TierChip';

const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });

function Points({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="space-y-1">
      <div className="font-mono text-[9.5px] tracking-widest text-faint uppercase">{label}</div>
      <ul className="space-y-1">
        {items.map(text => <li key={text} className="flex gap-2 leading-snug text-dim"><span className="text-faint">–</span><span>{text}</span></li>)}
      </ul>
    </div>
  );
}

/** One core question's page: the current answer, what confirmed sources agree on, what is debated or changed, what is
 * still open — and the sources it rests on. */
function Page({ page, exam }: { page: SynthesisPage; exam?: ExamCore }) {
  return (
    <article className="space-y-3 rounded-md border border-line p-3">
      <header className="space-y-1">
        <div className="flex items-start gap-2">
          <span className="pt-px font-mono text-[10.5px] text-faint">{page.core}</span>
          <h3 className="flex-1 text-[12.5px] leading-snug text-ink">{page.question}</h3>
          {exam?.score != null && <span className="shrink-0 font-mono text-[10px] text-faint" title="This core question's exam score">exam {exam.score}</span>}
        </div>
      </header>
      {page.summary ? (
        <p className="text-[12.5px] leading-relaxed text-ink">{page.summary}</p>
      ) : (
        <p className="text-faint">The graph knows nothing about this yet — the next learning cycle researches it.</p>
      )}
      <Points label="Agreed" items={page.consensus.map(p => p.text)} />
      <Points label="Debated or changed" items={page.debates.map(p => p.text)} />
      <Points label="Still open" items={page.summary ? page.open : []} />
      {page.sources.length > 0 && (
        <div className="space-y-1">
          <div className="font-mono text-[9.5px] tracking-widest text-faint uppercase">Key sources</div>
          <ul className="space-y-0.5">
            {page.sources.map(s => (
              <li key={s.url} className="flex items-center gap-1.5 text-[11px]">
                <a href={/^https?:/.test(s.url) ? s.url : undefined} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-1 text-dim hover:text-ink">
                  <ExternalLink size={10} className="shrink-0" /><span className="truncate">{s.title}</span>
                </a>
                <TierChip tier={s.tier} />
                <span className="ml-auto shrink-0 font-mono text-[9.5px] text-faint">{s.facts} fact{s.facts === 1 ? '' : 's'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-[10px] text-faint">Written {day(page.written_at)} from {page.facts} fact{page.facts === 1 ? '' : 's'}</p>
    </article>
  );
}

/**
 * What the domain knows, as a person reads it (kg/core/synthesis.py): a living review of each core question of the
 * charter, written from the graph's facts — every point cites them — and rewritten when they change.
 */
export function KnowledgePanel({ domain, editable, version }: { domain: string; editable: boolean; version: number }) {
  const [pages, setPages] = useState<SynthesisPage[] | null>(null);
  const [cores, setCores] = useState<ExamCore[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await kg.synthesis(domain);
      setPages(r.pages);
      setRunning(r.running);
      setError(null);
      kg.exam(domain).then(e => setCores(e.latest?.cores ?? [])).catch(() => undefined);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain]);
  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [running, load]);

  async function write() {
    try {
      const r = await kg.writeSynthesis(domain, true);
      if (r.skipped) setError(`Not written: ${r.skipped}`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!pages) return <div className="p-4 text-xs text-faint">{error ?? 'Loading…'}</div>;
  return (
    <div className="h-full space-y-4 overflow-y-auto p-4 text-[12px]">
      <div className="flex items-start gap-3">
        <p className="flex-1 leading-snug text-dim">
          A living review of your research charter: each core question answered from the knowledge graph's facts — every
          point rests on facts it cites — and rewritten after each learning cycle where they changed.
        </p>
        {editable && (
          <button onClick={() => void write()} disabled={running}
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1 text-[11px] text-dim hover:text-ink disabled:opacity-50">
            {running ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} {running ? 'Writing…' : 'Rewrite'}
          </button>
        )}
      </div>
      {error && <p className="text-xs text-bad">{error}</p>}
      {pages.length === 0 ? (
        <p className="rounded-md border border-line p-3 text-faint">
          {running ? 'Writing the first pages — a minute or so each with the local model.'
            : 'Nothing written yet. The review follows the research charter (Settings › Charter) and is written after each learning cycle.'}
        </p>
      ) : pages.map(p => <Page key={p.question} page={p} exam={cores.find(c => c.core === p.core)} />)}
    </div>
  );
}
