import { useEffect, useRef, useState } from 'react';
import cytoscape, { type Core } from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { History, Maximize2 } from 'lucide-react';
import type { Graph } from '../lib/api';
import { categoryColor } from '../lib/colors';

cytoscape.use(fcose);

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      'background-color': 'data(color)',
      width: 'data(size)',
      height: 'data(size)',
      label: 'data(name)',
      color: '#d5d9df',
      'font-size': 10,
      'font-weight': 500,
      'text-valign': 'bottom',
      'text-margin-y': 5,
      'text-outline-color': '#0a0c0f',
      'text-outline-width': 2,
      'border-width': 2,
      'border-color': '#0a0c0f',
    },
  },
  {
    // Synapse: thickness and brightness follow the weight (confirmed by more sources → stronger).
    selector: 'edge',
    style: {
      width: 'data(width)',
      'line-color': 'data(color)',
      'target-arrow-color': 'data(color)',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
      label: 'data(relation)',
      'font-size': 7.5,
      color: '#7c8592',
      'text-rotation': 'autorotate',
      'text-background-color': '#0a0c0f',
      'text-background-opacity': 1,
      'text-background-padding': '1px',
    },
  },
  // has_state facts are self-loops: keep them small so they don't clutter the graph.
  { selector: 'edge.state', style: { 'loop-direction': '-45deg', 'loop-sweep': '40deg', label: '' } },
  { selector: 'edge.superseded', style: { 'line-style': 'dashed', opacity: 0.35, label: '' } },
  { selector: '.dim', style: { opacity: 0.12 } },
  { selector: 'node.hl', style: { 'border-color': '#e2b356', 'border-width': 3 } },
  { selector: 'edge.hl', style: { 'line-color': '#e2b356', 'target-arrow-color': '#e2b356', color: '#e2b356' } },
  { selector: 'node:selected', style: { 'border-color': '#ffffff', 'border-width': 3 } },
];

/** Edge color: dim for weak facts, bright for well-confirmed ones. */
function weightColor(w: number): string {
  const v = Math.round(70 + w * 120);
  return `rgb(${v}, ${v + 8}, ${v + 20})`;
}

export function GraphView({ graph, entityTypes, highlight, selected, onSelect }: {
  graph: Graph;
  entityTypes: string[];
  highlight: Set<string>;
  selected: string | null;
  onSelect: (uid: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [showSuperseded, setShowSuperseded] = useState(false);

  useEffect(() => {
    const instance = cytoscape({ container: container.current, style: STYLE, minZoom: 0.2, maxZoom: 3 });
    instance.on('tap', 'node', e => onSelectRef.current(e.target.id()));
    instance.on('tap', e => { if (e.target === instance) onSelectRef.current(null); });
    cy.current = instance;
    // Panels and window resizes change the container: keep the canvas in sync.
    const observer = new ResizeObserver(() => {
      instance.resize();
      if (instance.nodes().length) instance.fit(undefined, 40);
    });
    observer.observe(container.current!);
    return () => { observer.disconnect(); instance.destroy(); };
  }, []);

  // Rebuild elements when the graph changes; keep positions of entities that already existed.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    const ids = new Set(graph.entities.map(e => e.uid));
    const old = new Map(c.nodes().map(n => [n.id(), n.position()]));
    c.elements().remove();
    c.add([
      ...graph.entities.map(e => ({
        group: 'nodes' as const,
        data: {
          id: e.uid,
          name: e.name.length > 28 ? e.name.slice(0, 27) + '…' : e.name,
          color: categoryColor(e.type, entityTypes),
          size: 16 + Math.min(20, Math.log2(1 + (e.mentions ?? 1)) * 7),
        },
        position: old.get(e.uid),
      })),
      ...graph.facts
        .filter(f => ids.has(f.source_uid) && ids.has(f.target_uid) && (f.valid || showSuperseded))
        .map(f => ({
          group: 'edges' as const,
          data: {
            id: f.uid,
            source: f.source_uid,
            target: f.target_uid,
            relation: f.relation,
            width: 0.8 + f.weight * 3.2,
            color: weightColor(f.weight),
          },
          classes: [f.source_uid === f.target_uid ? 'state' : '', f.valid ? '' : 'superseded'].join(' '),
        })),
    ]);
    const fresh = graph.entities.some(e => !old.has(e.uid));
    if (fresh && graph.entities.length) {
      c.layout({
        name: 'fcose', animate: old.size > 0, randomize: old.size === 0,
        nodeRepulsion: 9000, idealEdgeLength: 120, padding: 40,
      } as cytoscape.LayoutOptions).run();
    }
  }, [graph, entityTypes, showSuperseded]);

  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.elements().removeClass('dim hl');
    if (highlight.size === 0) return;
    const hit = c.nodes().filter(n => highlight.has(n.id()));
    const edges = hit.edgesWith(hit);
    c.elements().not(hit).not(edges).addClass('dim');
    hit.addClass('hl');
    edges.addClass('hl');
    if (hit.length) c.animate({ fit: { eles: hit, padding: 80 }, duration: 400 });
  }, [highlight, graph]);

  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.nodes().unselect();
    if (selected) c.getElementById(selected).select();
  }, [selected, graph]);

  const superseded = graph.facts.filter(f => !f.valid).length;

  return (
    <div className="absolute inset-0">
      <div ref={container} className="h-full w-full" />
      {graph.entities.length > 0 && (
        <>
          <div className="absolute right-3 bottom-3 flex gap-1.5">
            {superseded > 0 && (
              <button
                onClick={() => setShowSuperseded(v => !v)}
                className={`flex items-center gap-1 rounded border border-line-2 bg-panel px-2 py-1 text-[10.5px] ${showSuperseded ? 'text-gold' : 'text-dim hover:text-ink'}`}
                title="Show facts that newer information replaced"
              >
                <History size={12} /> superseded ({superseded})
              </button>
            )}
            <button
              onClick={() => cy.current?.animate({ fit: { eles: cy.current.elements(), padding: 40 }, duration: 300 })}
              className="rounded border border-line-2 bg-panel p-1.5 text-dim hover:text-ink"
              title="Fit graph"
            >
              <Maximize2 size={13} />
            </button>
          </div>
          <div className="absolute bottom-3 left-3 flex max-w-[65%] flex-wrap gap-x-3 gap-y-1">
            {entityTypes.map(t => (
              <span key={t} className="flex items-center gap-1.5 text-[10px] text-dim">
                <span className="h-2 w-2 rounded-full" style={{ background: categoryColor(t, entityTypes) }} />
                {t}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
