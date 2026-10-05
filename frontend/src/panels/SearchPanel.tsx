import { useState } from 'react';
import { ExternalLink, Loader2, Search } from 'lucide-react';
import { kg, type QueryResult } from '../lib/api';
import { categoryColor } from '../lib/colors';
import { FactRow } from '../components/FactRow';

export function SearchPanel({ domain, entityTypes, onHighlight, onSelect }: {
  domain: string;
  entityTypes: string[];
  onHighlight: (uids: Set<string>) => void;
  onSelect: (uid: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [k, setK] = useState(5);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setBusy(true); setError(null);
    try {
      const r = await kg.query(domain, query.trim(), k);
      setResult(r);
      onHighlight(new Set(r.entities.map(x => x.uid)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const hits = result?.entities.filter(e => e.similarity !== undefined) ?? [];

  return (
    <div className="flex h-full flex-col">
      <form onSubmit={submit} className="flex gap-2 border-b border-line p-3">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Ask the graph by meaning…"
          className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] outline-none focus:border-gold/60"
        />
        <select
          value={k}
          onChange={e => setK(Number(e.target.value))}
          className="rounded-md border border-line-2 bg-bg px-2 text-xs text-dim"
          title="Number of matches"
        >
          {[3, 5, 10, 20].map(n => <option key={n} value={n}>top {n}</option>)}
        </select>
        <button type="submit" disabled={busy || !query.trim()} className="rounded-md bg-gold px-3 text-black disabled:opacity-40">
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
        </button>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {error && <p className="p-2 text-xs text-bad">{error}</p>}
        {!result && !error && (
          <p className="p-3 text-xs leading-relaxed text-faint">
            Graph RAG: finds the entities and facts closest to your question by meaning, then the strongest facts
            around them, with the sources behind them. This is the same read path the Head Agent uses.
          </p>
        )}
        {result && (
          <>
            {hits.length > 0 && (
              <div className="flex flex-wrap gap-1.5 px-2.5 pt-1 pb-2">
                {hits.map(e => (
                  <button
                    key={e.uid}
                    onClick={() => onSelect(e.uid)}
                    className="flex items-center gap-1.5 rounded-full border border-line-2 px-2.5 py-1 text-[11.5px] hover:border-gold/60"
                    title={`similarity ${e.similarity}`}
                  >
                    <span className="h-2 w-2 rounded-full" style={{ background: categoryColor(e.type, entityTypes) }} />
                    {e.name}
                  </button>
                ))}
              </div>
            )}
            <h3 className="px-2.5 pt-1 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Facts</h3>
            {result.facts.length === 0 && <p className="px-2.5 text-xs text-faint">Nothing relevant in this graph yet.</p>}
            {result.facts.map(f => <FactRow key={f.uid} fact={f} onSelect={onSelect} />)}
            {result.episodes.length > 0 && (
              <>
                <h3 className="px-2.5 pt-4 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Sources</h3>
                <ul className="space-y-1 px-2.5 pb-3">
                  {result.episodes.map(ep => (
                    <li key={ep.uid} className="text-[11px]">
                      {/^https?:\/\//.test(ep.source) ? (
                        <a href={ep.source} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-dim hover:text-ink">
                          <ExternalLink size={10} className="shrink-0" /><span className="truncate">{ep.title || ep.source}</span>
                        </a>
                      ) : <span className="text-dim">{ep.title || ep.source}</span>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
