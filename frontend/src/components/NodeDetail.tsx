import { ArrowLeft, ArrowRight, X } from 'lucide-react';
import type { Graph, GraphNode } from '../lib/api';
import { categoryColor } from '../lib/colors';

export function NodeDetail({ node, graph, entityTypes, onClose, onSelect }: {
  node: GraphNode;
  graph: Graph;
  entityTypes: string[];
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const byId = new Map(graph.nodes.map(n => [n.id, n]));
  const out = graph.edges.filter(e => e.from === node.id);
  const inc = graph.edges.filter(e => e.to === node.id);

  const link = (id: string) => {
    const other = byId.get(id);
    return (
      <button onClick={() => onSelect(id)} className="truncate text-left text-dim hover:text-ink">
        {other?.label ?? id}
      </button>
    );
  };

  return (
    <div className="absolute top-3 right-3 z-10 max-h-[calc(100%-24px)] w-80 overflow-y-auto rounded-lg border border-line-2 bg-panel/95 p-4 shadow-2xl backdrop-blur max-sm:left-3 max-sm:w-auto">
      <div className="mb-2 flex items-start justify-between gap-2">
        <span
          className="rounded px-1.5 py-0.5 font-mono text-[10px]"
          style={{ background: categoryColor(node.category, entityTypes) + '26', color: categoryColor(node.category, entityTypes) }}
        >
          {node.category}
        </span>
        <button onClick={onClose} className="text-faint hover:text-ink"><X size={14} /></button>
      </div>
      <p className="text-[13px] leading-relaxed">{node.content}</p>

      <dl className="mt-3 space-y-1 text-[11px]">
        <div className="flex gap-2"><dt className="w-16 text-faint">reliability</dt>
          <dd className="flex flex-1 items-center gap-2">
            <span className="h-1 flex-1 rounded bg-line"><span className="block h-1 rounded bg-gold" style={{ width: `${node.reliability * 100}%` }} /></span>
            <span className="font-mono">{node.reliability.toFixed(2)}</span>
          </dd>
        </div>
        <div className="flex gap-2"><dt className="w-16 text-faint">source</dt><dd className="break-all text-dim">{node.source}</dd></div>
        <div className="flex gap-2"><dt className="w-16 text-faint">uid</dt><dd className="font-mono break-all text-faint">{node.id}</dd></div>
      </dl>

      {node.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {node.tags.map(t => <span key={t} className="rounded bg-panel-2 px-1.5 py-0.5 text-[10px] text-dim">{t}</span>)}
        </div>
      )}

      {(out.length > 0 || inc.length > 0) && (
        <div className="mt-3 space-y-1.5 border-t border-line pt-3 text-[11px]">
          {out.map((e, i) => (
            <div key={`o${i}`} className="flex items-center gap-1.5">
              <ArrowRight size={11} className="shrink-0 text-gold" />
              <span className="shrink-0 font-mono text-faint">{e.relation}</span>
              {link(e.to)}
            </div>
          ))}
          {inc.map((e, i) => (
            <div key={`i${i}`} className="flex items-center gap-1.5">
              <ArrowLeft size={11} className="shrink-0 text-faint" />
              <span className="shrink-0 font-mono text-faint">{e.relation}</span>
              {link(e.from)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
