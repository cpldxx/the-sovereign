import { useEffect, useRef } from 'react';
import cytoscape, { type Core } from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { Maximize2 } from 'lucide-react';
import type { Graph } from '../lib/api';
import { categoryColor } from '../lib/colors';

cytoscape.use(fcose);

const short = (s: string, n = 46) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      'background-color': 'data(color)',
      width: 'data(size)',
      height: 'data(size)',
      label: 'data(short)',
      color: '#c9ced5',
      'font-size': 9,
      'text-wrap': 'wrap',
      'text-max-width': '140px',
      'text-valign': 'bottom',
      'text-margin-y': 5,
      'border-width': 2,
      'border-color': '#0a0c0f',
    },
  },
  {
    selector: 'edge',
    style: {
      width: 'data(width)',
      'line-color': '#3a4350',
      'target-arrow-color': '#3a4350',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
      label: 'data(relation)',
      'font-size': 7.5,
      color: '#6b7480',
      'text-rotation': 'autorotate',
      'text-background-color': '#0a0c0f',
      'text-background-opacity': 1,
      'text-background-padding': '1px',
    },
  },
  { selector: '.dim', style: { opacity: 0.15 } },
  { selector: 'node.hl', style: { 'border-color': '#e2b356', 'border-width': 3 } },
  { selector: 'edge.hl', style: { 'line-color': '#e2b356', 'target-arrow-color': '#e2b356', color: '#e2b356' } },
  { selector: 'node:selected', style: { 'border-color': '#ffffff', 'border-width': 3 } },
];

export function GraphView({ graph, entityTypes, highlight, selected, onSelect }: {
  graph: Graph;
  entityTypes: string[];
  highlight: Set<string>;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const instance = cytoscape({
      container: container.current,
      style: STYLE,
      minZoom: 0.2,
      maxZoom: 3,
    });
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

  // Rebuild elements when the graph changes; keep positions of nodes that already existed.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    const ids = new Set(graph.nodes.map(n => n.id));
    const old = new Map(c.nodes().map(n => [n.id(), n.position()]));
    c.elements().remove();
    c.add([
      ...graph.nodes.map(n => ({
        group: 'nodes' as const,
        data: {
          id: n.id,
          short: short(n.label),
          color: categoryColor(n.category, entityTypes),
          size: 14 + n.reliability * 18,
        },
        position: old.get(n.id),
      })),
      ...graph.edges
        .filter(e => ids.has(e.from) && ids.has(e.to))
        .map((e, i) => ({
          group: 'edges' as const,
          data: { id: `e${i}:${e.from}:${e.to}`, source: e.from, target: e.to, relation: e.relation, width: 1 + e.weight * 2 },
        })),
    ]);
    const fresh = graph.nodes.some(n => !old.has(n.id));
    if (fresh && graph.nodes.length) {
      c.layout({ name: 'fcose', animate: old.size > 0, randomize: old.size === 0, nodeRepulsion: 9000, idealEdgeLength: 110, padding: 40 } as cytoscape.LayoutOptions).run();
    }
  }, [graph, entityTypes]);

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

  return (
    <div className="absolute inset-0">
      <div ref={container} className="h-full w-full" />
      {graph.nodes.length > 0 && (
        <>
          <button
            onClick={() => cy.current?.animate({ fit: { eles: cy.current.elements(), padding: 40 }, duration: 300 })}
            className="absolute right-3 bottom-3 rounded border border-line-2 bg-panel p-1.5 text-dim hover:text-ink"
            title="Fit graph"
          >
            <Maximize2 size={13} />
          </button>
          <div className="absolute bottom-3 left-3 flex max-w-[70%] flex-wrap gap-x-3 gap-y-1">
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
