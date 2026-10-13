import { useEffect, useState } from 'react';
import { Network, Search, X } from 'lucide-react';
import { kg, type Graph } from '../lib/api';
import { SearchPanel } from '../panels/SearchPanel';
import { GraphView } from './GraphView';

/**
 * The knowledge graph tab. Mounted only while open (a live simulation of hundreds of nodes costs CPU even when
 * unseen), and it draws the most connected entities — LIMITS — plus whatever the Head just talked about; the graph
 * itself keeps everything. Search opens beside it: what it finds lights up in the graph.
 */
const LIMITS = [300, 1000, 0] as const;
const LIMIT_KEY = 'sovereign.graphLimit';

export function GraphPane({ domain, entityTypes, highlight, selected, version, onSelect, onHighlight, onClearHighlight }: {
  domain: string;
  entityTypes: string[];
  highlight: Set<string>;
  selected: string | null;
  version: number;
  onSelect: (uid: string | null) => void;
  onHighlight: (uids: Set<string>) => void;
  onClearHighlight: () => void;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [searching, setSearching] = useState(false);
  const [limit, setLimit] = useState<number>(() => {
    try { return Number(localStorage.getItem(LIMIT_KEY) ?? 300); } catch { return 300; }
  });
  const [error, setError] = useState<string | null>(null);
  const include = [...highlight].sort().join(',');

  useEffect(() => {
    let alive = true;
    kg.graph(domain, limit, include ? include.split(',') : [])
      .then(g => { if (alive) { setGraph(g); setError(null); } })
      .catch(e => alive && setError((e as Error).message));
    return () => { alive = false; };
  }, [domain, limit, include, version]);

  function choose(n: number) {
    setLimit(n);
    try { localStorage.setItem(LIMIT_KEY, String(n)); } catch { /* per-viewer convenience only */ }
  }

  const shown = graph?.entities.length ?? 0, total = graph?.total?.entities ?? shown;
  return (
    <div className="absolute inset-0">
      {graph && (
        <GraphView graph={graph} entityTypes={entityTypes} highlight={highlight} selected={selected} onSelect={onSelect} />
      )}
      {graph && graph.entities.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-center">
          <Network className="text-faint" size={32} />
          <p className="text-sm text-dim">This knowledge graph is empty.</p>
          <p className="text-xs text-faint">Ingest text, or tell the Head Agent something worth keeping.</p>
        </div>
      )}
      {error && <p className="absolute top-3 right-3 text-xs text-bad">{error}</p>}
      <div className="absolute top-3 right-3 flex items-center gap-2 text-[11px] text-dim">
        {total > 0 && <span>showing {shown} of {total}</span>}
        <div className="flex overflow-hidden rounded border border-line-2">
          {LIMITS.map(n => (
            <button key={n} onClick={() => choose(n)}
              className={`px-2 py-0.5 ${limit === n ? 'bg-accent/15 text-accent' : 'bg-panel hover:text-ink'}`}>
              {n === 0 ? 'all' : n}
            </button>
          ))}
        </div>
      </div>
      <div className={`absolute top-3 flex gap-2 ${searching ? 'left-[352px]' : 'left-3'}`}>
        {!searching && (
          <button onClick={() => setSearching(true)}
            className="flex items-center gap-1.5 rounded border border-line-2 bg-panel px-2.5 py-1 text-[11px] text-dim hover:text-ink">
            <Search size={11} /> Search the graph
          </button>
        )}
        {highlight.size > 0 && (
          <button onClick={onClearHighlight}
            className="rounded border border-line-2 bg-panel px-2.5 py-1 text-[11px] text-dim hover:text-ink">
            Clear highlight ({highlight.size})
          </button>
        )}
      </div>
      {searching && (
        <div className="absolute inset-y-0 left-0 z-10 flex w-[340px] max-w-full flex-col border-r border-line bg-panel">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="font-mono text-[10px] tracking-widest text-faint uppercase">Search the graph</span>
            <button onClick={() => setSearching(false)} className="rounded p-1 text-faint hover:text-ink" title="Close"><X size={13} /></button>
          </div>
          <div className="min-h-0 flex-1">
            <SearchPanel domain={domain} entityTypes={entityTypes} onHighlight={onHighlight} onSelect={onSelect} />
          </div>
        </div>
      )}
    </div>
  );
}
