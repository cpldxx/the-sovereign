import { useEffect, useState } from 'react';
import { ArrowUpRight, Check, GitMerge, Hourglass, Loader2, Plus, X } from 'lucide-react';
import { kg, type IngestFactItem, type IngestResult } from '../lib/api';

const STAGES = ['Extracting entities and facts', 'Validating against the source', 'Resolving entities', 'Linking facts'];

const FACT_ICON: Record<IngestFactItem['status'], React.ReactNode> = {
  created: <Plus size={12} className="text-good" />,
  strengthened: <ArrowUpRight size={12} className="text-gold" />,
  review: <Hourglass size={12} className="text-gold" />,
  rejected: <X size={12} className="text-bad" />,
};

export function IngestPanel({ domain, onIngested, ontologyReady }: {
  domain: string;
  onIngested: (touchedUids: string[]) => Promise<void>;
  ontologyReady: boolean;
}) {
  const [text, setText] = useState('');
  const [source, setSource] = useState('');
  const [partial, setPartial] = useState(false);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!busy) return;
    const start = Date.now();
    const t = setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true); setElapsed(0); setError(null); setResult(null);
    try {
      const r = await kg.ingest(domain, text.trim(), source.trim() || 'user_input', undefined, partial ? 'partial' : 'full');
      setResult(r);
      if (!r.duplicate) setText('');
      await onIngested(r.touched_uids);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The pipeline is one request; the stage shown is a rough guide from elapsed time.
  const stage = STAGES[Math.min(STAGES.length - 1, Math.floor(elapsed / 9))];

  return (
    <div className="flex h-full flex-col overflow-y-auto p-4">
      <form onSubmit={submit} className="space-y-3">
        {!ontologyReady && (
          <p className="rounded-md border border-gold/30 bg-gold/10 px-3 py-2 text-[11px] text-gold">
            The ontology is still being generated. Ingesting now uses the default grammar — waiting a moment gives
            domain-specific types.
          </p>
        )}
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={9}
          placeholder="Paste an article, notes, a report… Entities become nodes, facts become weighted connections. A fact confirmed by another source gets stronger; a newer value supersedes the old one."
          className="w-full resize-y rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] leading-relaxed outline-none focus:border-gold/60"
        />
        <input
          value={source}
          onChange={e => setSource(e.target.value)}
          placeholder="Source (URL, paper, report)"
          className="w-full rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] outline-none focus:border-gold/60"
        />
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-[11.5px] text-dim" title="Only part of the source was readable (e.g. paywall) — its facts are trusted less">
            <input type="checkbox" checked={partial} onChange={e => setPartial(e.target.checked)} className="accent-[#e2b356]" />
            partial source
          </label>
          <button
            type="submit"
            disabled={busy || !text.trim()}
            className="flex items-center gap-2 rounded-md bg-gold px-4 py-1.5 text-sm font-medium text-black disabled:opacity-40"
          >
            {busy && <Loader2 size={14} className="animate-spin" />} Ingest
          </button>
        </div>
      </form>

      {busy && (
        <p className="mt-4 flex items-center gap-2 text-xs text-dim">
          <Loader2 size={12} className="animate-spin text-gold" /> {stage}… {elapsed}s
          <span className="text-faint">(usually under a minute)</span>
        </p>
      )}

      {error && <p className="mt-4 text-xs text-bad">{error}</p>}

      {result?.duplicate && <p className="mt-4 text-xs text-gold">This exact text was already ingested — nothing changed.</p>}

      {result && !result.duplicate && (
        <div className="mt-5 space-y-4">
          <div className="grid grid-cols-4 gap-2 text-center">
            {([
              ['new entities', result.entities_created],
              ['known', result.entities_matched],
              ['new facts', result.facts_created],
              ['strengthened', result.facts_strengthened],
              ['superseded', result.facts_invalidated],
              ['to review', result.review_items],
              ['rejected', result.facts_rejected],
              ['dropped', result.entities_dropped],
            ] as const).map(([k, v]) => (
              <div key={k} className="rounded-md bg-panel-2 py-2">
                <div className="font-mono text-base">{v}</div>
                <div className="text-[9.5px] tracking-wider text-faint uppercase">{k}</div>
              </div>
            ))}
          </div>

          <section>
            <h4 className="mb-1.5 font-mono text-[10px] tracking-widest text-faint uppercase">Entities</h4>
            <div className="flex flex-wrap gap-1.5">
              {result.entities.map((e, i) => (
                <span
                  key={i}
                  title={e.reason ?? `${e.type} · ${e.status}`}
                  className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${
                    e.status === 'dropped' ? 'border-line text-faint line-through'
                      : e.status === 'matched' ? 'border-gold/40 text-ink' : 'border-good/40 text-ink'
                  }`}
                >
                  {e.status === 'matched' ? <GitMerge size={10} className="text-gold" /> : e.status === 'created' ? <Plus size={10} className="text-good" /> : null}
                  {e.name}
                </span>
              ))}
            </div>
          </section>

          <section>
            <h4 className="mb-1.5 font-mono text-[10px] tracking-widest text-faint uppercase">Facts</h4>
            <ul className="space-y-2">
              {result.facts.map((f, i) => (
                <li key={i} className="flex gap-2 text-[11.5px]">
                  <span className="mt-0.5 shrink-0">{FACT_ICON[f.status]}</span>
                  <div className="min-w-0">
                    <div className="font-mono text-[10px] text-faint">
                      {f.source}{f.source !== f.target ? ` —${f.relation}→ ${f.target}` : ' · state'} · {f.status}
                      {f.invalidated && f.invalidated.length > 0 && <span className="text-gold"> · superseded {f.invalidated.length}</span>}
                    </div>
                    <div className={f.status === 'rejected' ? 'text-faint' : 'text-ink'}>{f.fact}</div>
                    {f.reason && <div className="text-faint">{f.reason}</div>}
                  </div>
                </li>
              ))}
            </ul>
            {result.facts.length === 0 && <p className="text-xs text-faint">No facts could be extracted.</p>}
          </section>
          {result.review_items > 0 && (
            <p className="flex items-center gap-1.5 text-[11.5px] text-gold">
              <Check size={12} /> {result.review_items} item{result.review_items === 1 ? '' : 's'} sent to the review queue
            </p>
          )}
        </div>
      )}
    </div>
  );
}
