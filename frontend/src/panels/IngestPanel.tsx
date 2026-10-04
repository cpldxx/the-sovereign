import { useEffect, useState } from 'react';
import { Check, Loader2, X } from 'lucide-react';
import { kg, type IngestResult } from '../lib/api';

const STAGES = ['Extracting facts', 'Checking ontology', 'Validating against source', 'Embedding', 'Storing', 'Linking'];

export function IngestPanel({ domain, onIngested, ontologyReady }: {
  domain: string;
  onIngested: (storedUids: string[]) => Promise<void>;
  ontologyReady: boolean;
}) {
  const [text, setText] = useState('');
  const [source, setSource] = useState('');
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
      const r = await kg.ingest(domain, text.trim(), source.trim() || 'user_input');
      setResult(r);
      if (r.stored > 0) setText('');
      await onIngested(r.details.filter(d => d.status === 'stored').map(d => d.uid));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The pipeline is one request; the stage shown is a rough guide from elapsed time.
  const stage = STAGES[Math.min(STAGES.length - 1, Math.floor(elapsed / 18))];

  return (
    <div className="flex h-full flex-col overflow-y-auto p-4">
      <form onSubmit={submit} className="space-y-3">
        {!ontologyReady && (
          <p className="rounded-md border border-gold/30 bg-gold/10 px-3 py-2 text-[11px] text-gold">
            The ontology is still being generated. Ingesting now uses the default grammar — waiting a few minutes gives
            domain-specific types.
          </p>
        )}
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={10}
          placeholder="Paste an article, notes, a report… The pipeline extracts facts, keeps only what fits the ontology and is supported by the text, then links them into the graph."
          className="w-full resize-y rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] leading-relaxed outline-none focus:border-gold/60"
        />
        <div className="flex gap-2">
          <input
            value={source}
            onChange={e => setSource(e.target.value)}
            placeholder="Source (URL, paper, report)"
            className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] outline-none focus:border-gold/60"
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            className="flex items-center gap-2 rounded-md bg-gold px-4 text-sm font-medium text-black disabled:opacity-40"
          >
            {busy && <Loader2 size={14} className="animate-spin" />} Ingest
          </button>
        </div>
      </form>

      {busy && (
        <p className="mt-4 flex items-center gap-2 text-xs text-dim">
          <Loader2 size={12} className="animate-spin text-gold" /> {stage}… {elapsed}s
          <span className="text-faint">(usually 1–3 min)</span>
        </p>
      )}

      {error && <p className="mt-4 text-xs text-bad">{error}</p>}

      {result && (
        <div className="mt-5 space-y-3">
          <div className="grid grid-cols-4 gap-2 text-center">
            {([['extracted', result.total], ['stored', result.stored], ['rejected', result.rejected], ['edges', result.edges_created]] as const).map(([k, v]) => (
              <div key={k} className="rounded-md bg-panel-2 py-2">
                <div className="font-mono text-base">{v}</div>
                <div className="text-[10px] tracking-wider text-faint uppercase">{k}</div>
              </div>
            ))}
          </div>
          <ul className="space-y-1.5">
            {result.details.map(d => (
              <li key={d.uid} className="flex gap-2 text-[11.5px]">
                {d.status === 'stored'
                  ? <Check size={13} className="mt-0.5 shrink-0 text-good" />
                  : <X size={13} className="mt-0.5 shrink-0 text-bad" />}
                <div className="min-w-0">
                  <div className="truncate font-mono text-dim">{d.uid.split(':').slice(1, 2)} · {d.status}</div>
                  {d.reason && <div className="text-faint">{d.reason}</div>}
                </div>
              </li>
            ))}
          </ul>
          {result.total === 0 && <p className="text-xs text-faint">No facts could be extracted from this text.</p>}
        </div>
      )}
    </div>
  );
}
