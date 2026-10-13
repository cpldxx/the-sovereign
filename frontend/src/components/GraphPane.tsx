import { useEffect, useState } from 'react';
import { Network } from 'lucide-react';
import { kg, type Graph } from '../lib/api';
import { GraphView } from './GraphView';

/**
 * The knowledge graph tab. Mounted only while open (a live simulation of hundreds of nodes costs CPU even when
 * unseen), and it draws the most connected entities — LIMITS — plus whatever the Head just talked about; the graph
 * itself keeps everything.
 */
const LIMITS = [300, 1000, 0] as const;
const LIMIT_KEY = 'sovereign.graphLimit';

export function GraphPane({ domain, entityTypes, highlight, selected, version, onSelect, onClearHighlight }: {
  domain: string;
  entityTypes: string[];
  highlight: Set<string>;
  selected: string | null;
  version: number;
  onSelect: (uid: string | null) => void;
  onClearHighlight: () => void;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
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
              className={`px-2 py-0.5 ${limit === n ? 'bg-gold/15 text-gold' : 'bg-panel hover:text-ink'}`}>
              {n === 0 ? 'all' : n}
            </button>
          ))}
        </div>
      </div>
      {highlight.size > 0 && (
        <button onClick={onClearHighlight}
          className="absolute top-3 left-3 rounded border border-line-2 bg-panel px-2.5 py-1 text-[11px] text-dim hover:text-ink">
          Clear highlight ({highlight.size})
        </button>
      )}
    </div>
  );
}
