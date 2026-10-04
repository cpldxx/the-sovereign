import { useState } from 'react';
import { Loader2, Search } from 'lucide-react';
import { kg, type KGNode, type QueryResult } from '../lib/api';
import { categoryColor } from '../lib/colors';

function NodeRow({ node, entityTypes, onSelect, score }: {
  node: KGNode;
  entityTypes: string[];
  onSelect: (uid: string) => void;
  score?: number;
}) {
  return (
    <button onClick={() => onSelect(node.uid)} className="w-full rounded-md px-2.5 py-2 text-left hover:bg-panel-2">
      <div className="mb-0.5 flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: categoryColor(node.category, entityTypes) }} />
        <span className="font-mono text-[10.5px] text-faint">{node.category}</span>
        {score !== undefined && (
          <span className="ml-auto flex items-center gap-1.5">
            <span className="h-1 w-14 rounded bg-line">
              <span className="block h-1 rounded bg-gold" style={{ width: `${Math.max(0, 1 - score) * 100}%` }} />
            </span>
            <span className="font-mono text-[10px] text-faint">{(1 - score).toFixed(2)}</span>
          </span>
        )}
      </div>
      <p className="text-[12.5px] leading-snug text-ink">{node.content}</p>
    </button>
  );
}

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
      onHighlight(new Set([...r.matches, ...r.neighbors].map(n => n.uid)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const known = new Map([...(result?.matches ?? []), ...(result?.neighbors ?? [])].map(n => [n.uid, n.content]));
  const label = (uid: string) => known.get(uid) ?? uid;

  return (
    <div className="flex h-full flex-col">
      <form onSubmit={submit} className="flex gap-2 border-b border-line p-3">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search by meaning…"
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
            Semantic search over the graph (Graph RAG): finds the closest facts by meaning, then follows their edges one
            hop. This is the same read path the Head Agent uses.
          </p>
        )}
        {result && (
          <>
            <h3 className="px-2.5 pt-1 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">
              Matches · similarity
            </h3>
            {result.matches.length === 0 && <p className="px-2.5 text-xs text-faint">Nothing embedded in this domain yet.</p>}
            {result.matches.map(n => (
              <NodeRow key={n.uid} node={n} score={n.distance} entityTypes={entityTypes} onSelect={onSelect} />
            ))}
            {result.neighbors.length > 0 && (
              <>
                <h3 className="px-2.5 pt-4 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">
                  Connected (1 hop)
                </h3>
                {result.neighbors.map(n => (
                  <NodeRow key={n.uid} node={n} entityTypes={entityTypes} onSelect={onSelect} />
                ))}
              </>
            )}
            {result.edges.length > 0 && (
              <>
                <h3 className="px-2.5 pt-4 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Relations</h3>
                <ul className="space-y-2 px-2.5 pb-3 text-[11px] text-dim">
                  {result.edges.map((e, i) => (
                    <li key={i}>
                      <div className="truncate">{label(e.from_node)}</div>
                      <div className="font-mono text-[10.5px] text-gold">↳ {e.relation}</div>
                      <div className="truncate">{label(e.to_node)}</div>
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
