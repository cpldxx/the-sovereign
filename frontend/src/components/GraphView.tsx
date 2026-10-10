import { useEffect, useRef, useState } from 'react';
import cytoscape, { type Core } from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { History, Maximize2 } from 'lucide-react';
import type { Entity, Graph } from '../lib/api';
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
      // Zoomed out, 800 labels are noise: they appear once they'd be readable.
      'min-zoomed-font-size': 9,
    },
  },
  // Entities no fact connects yet: the outer halo, quieter than the connected graph.
  { selector: 'node.lone', style: { opacity: 0.7, 'border-width': 1 } },
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
      'min-zoomed-font-size': 8,
    },
  },
  // has_state facts are self-loops: keep them small so they don't clutter the graph.
  { selector: 'edge.state', style: {
    'loop-direction': '-45deg', 'loop-sweep': '30deg', 'control-point-step-size': 6, label: '',
    width: 0.6, opacity: 0.3, 'target-arrow-shape': 'none',
  } },
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

/** Golden angle: successive points of a sunflower never line up, so the halo stays round and even. */
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const CORE_MIN = 8;          // components at least this big are laid out by force; smaller ones sit on a ring

/** Connected components over the drawn (non-loop) edges, biggest first. */
function components(ids: string[], links: [string, string][]): string[][] {
  const adj = new Map(ids.map(id => [id, [] as string[]]));
  for (const [a, b] of links) { adj.get(a)!.push(b); adj.get(b)!.push(a); }
  const seen = new Set<string>();
  const out: string[][] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    const comp: string[] = [];
    const stack = [id];
    seen.add(id);
    while (stack.length) {
      const x = stack.pop()!;
      comp.push(x);
      for (const y of adj.get(x)!) if (!seen.has(y)) { seen.add(y); stack.push(y); }
    }
    out.push(comp);
  }
  return out.sort((a, b) => b.length - a.length);
}

/**
 * A round composition instead of fcose's tiling (which packs hundreds of unconnected entities into a square):
 * the big connected components in the middle (force layout), the small clusters on a ring around them, and the
 * entities no fact connects yet as a sunflower halo outside — sorted by type, so the halo reads in color bands.
 */
function layoutRound(c: Core, comps: string[][], byId: Map<string, Entity>, randomize: boolean) {
  const core = comps.filter(x => x.length >= CORE_MIN);
  const small = comps.filter(x => x.length > 1 && x.length < CORE_MIN);
  const lone = comps.filter(x => x.length === 1).map(x => x[0]);
  const coreIds = new Set(core.flat());
  const coreNodes = c.nodes().filter(n => coreIds.has(n.id()));

  const place = () => {
    // The core's center and radius (from the nodes themselves, not a box: the blob is round-ish).
    let cx = 0, cy = 0, radius = 0;
    if (coreNodes.length) {
      coreNodes.forEach(n => { cx += n.position('x'); cy += n.position('y'); });
      cx /= coreNodes.length; cy /= coreNodes.length;
      // Most of the core, not its farthest branch tip: a few long arms would push the ring far out.
      const dist: number[] = [];
      coreNodes.forEach(n => { dist.push(Math.hypot(n.position('x') - cx, n.position('y') - cy)); });
      dist.sort((a, b) => a - b);
      radius = dist[Math.floor(dist.length * 0.85)] + 20;
    }
    const positions = new Map<string, { x: number; y: number }>();

    // Small clusters on a ring, each drawn as its own little circle, grouped by their main type.
    const typeOf = (ids: string[]) => byId.get(ids[0])?.type ?? '';
    const clusters = [...small].sort((a, b) => typeOf(a).localeCompare(typeOf(b)));
    const span = (k: number) => 2 * (12 + 8 * k) + 20;          // a cluster's footprint on the ring
    const total = clusters.reduce((s, k) => s + span(k.length), 0);
    const ring = Math.max(radius + 50, total / (2 * Math.PI));
    let angle = 0;
    for (const comp of clusters) {
      const share = (span(comp.length) / Math.max(total, 1)) * 2 * Math.PI;
      const a = angle + share / 2;
      angle += share;
      const ccx = cx + ring * Math.cos(a), ccy = cy + ring * Math.sin(a);
      const r = comp.length === 2 ? 18 : 12 + 8 * comp.length;
      comp.forEach((id, i) => {
        const b = a + (2 * Math.PI * i) / comp.length;
        positions.set(id, { x: ccx + r * Math.cos(b), y: ccy + r * Math.sin(b) });
      });
    }

    // The halo: a sunflower annulus outside everything else.
    const inner = (clusters.length ? ring + 45 : radius + 45);
    const area = 28 * 28;
    const order = [...lone].sort((a, b) => {
      const ea = byId.get(a)!, eb = byId.get(b)!;
      return ea.type.localeCompare(eb.type) || (eb.mentions ?? 0) - (ea.mentions ?? 0);
    });
    order.forEach((id, i) => {
      const r = Math.sqrt(inner * inner + ((i + 0.5) * area) / Math.PI);
      const th = i * GOLDEN;
      positions.set(id, { x: cx + r * Math.cos(th), y: cy + r * Math.sin(th) });
    });

    c.nodes().filter(n => positions.has(n.id())).positions(n => positions.get(n.id())!);
    c.fit(undefined, 40);
  };

  if (!coreNodes.length) { place(); return; }
  const run = coreNodes.union(coreNodes.edgesWith(coreNodes)).layout({
    name: 'fcose', animate: false, randomize, quality: 'default',
    nodeRepulsion: 4500, idealEdgeLength: 70, edgeElasticity: 0.45, gravity: 0.5, gravityRange: 3.0,
    packComponents: true, nodeSeparation: 40, padding: 40,
  } as cytoscape.LayoutOptions);
  run.one('layoutstop', place);
  run.run();
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
    const instance = cytoscape({ container: container.current, style: STYLE, minZoom: 0.05, maxZoom: 3 });
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
    const drawn = graph.facts.filter(f => ids.has(f.source_uid) && ids.has(f.target_uid) && (f.valid || showSuperseded));
    const comps = components(graph.entities.map(e => e.uid),
      drawn.filter(f => f.source_uid !== f.target_uid).map(f => [f.source_uid, f.target_uid] as [string, string]));
    const lone = new Set(comps.filter(x => x.length === 1).map(x => x[0]));
    c.elements().remove();
    c.add([
      ...graph.entities.map(e => ({
        group: 'nodes' as const,
        data: {
          id: e.uid,
          name: e.name.length > 28 ? e.name.slice(0, 27) + '…' : e.name,
          color: categoryColor(e.type, entityTypes),
          size: (16 + Math.min(20, Math.log2(1 + (e.mentions ?? 1)) * 7)) * (lone.has(e.uid) ? 0.7 : 1),
        },
        classes: lone.has(e.uid) ? 'lone' : '',
        position: old.get(e.uid),
      })),
      ...drawn
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
      layoutRound(c, comps, new Map(graph.entities.map(e => [e.uid, e])), old.size === 0);
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
