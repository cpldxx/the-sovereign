import { useEffect, useRef, useState } from 'react';
import ForceGraph from 'force-graph';
import { forceX, forceY, type ForceLink, type ForceManyBody } from 'd3-force';
import { History, Maximize2 } from 'lucide-react';
import type { Graph } from '../lib/api';
import { categoryColor } from '../lib/colors';

/**
 * The knowledge graph drawn the way Obsidian draws a vault: a live force simulation on a canvas. Small dots sized by
 * their links, thin faint links without arrows or labels, names fading in as you zoom, hover lighting a node and its
 * neighbours while the rest dims. Entities no fact connects yet float as a cloud around the connected graph (the
 * centering forces keep the whole thing round). Nodes keep their place across refreshes; dragging one shakes the
 * rest like in Obsidian.
 */

interface Node {
  id: string;
  name: string;
  color: string;
  degree: number;
  lone: boolean;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
}

interface Link {
  id: string;
  source: string | Node;
  target: string | Node;
  weight: number;
  valid: boolean;
}

const BG = '#0a0c0f';
const GOLD = '#e2b356';
const LABEL = '200, 206, 214';
const LABEL_FROM = 1.4;      // zoom where names start to fade in
const LABEL_FULL = 2.4;      // … and are fully shown

const endId = (end: string | Node) => (typeof end === 'string' ? end : end.id);
const radius = (n: Node) => (n.lone ? 1.8 : 2.2 + Math.sqrt(n.degree) * 1.3);

function alpha(hex: string, a: number): string {
  const v = parseInt(hex.slice(1), 16);
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${a})`;
}

export function GraphView({ graph, entityTypes, highlight, selected, onSelect }: {
  graph: Graph;
  entityTypes: string[];
  highlight: Set<string>;
  selected: string | null;
  onSelect: (uid: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const fg = useRef<ForceGraph<Node, Link> | null>(null);
  const nodes = useRef(new Map<string, Node>());
  const neighbours = useRef(new Map<string, Set<string>>());
  // What the painters read; refs, so hover and props repaint without rebuilding the simulation.
  const view = useRef({ hover: null as string | null, highlight, selected });
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [showSuperseded, setShowSuperseded] = useState(false);
  const fitted = useRef(false);

  /** Setting a painter again is force-graph's way to ask for one more frame. */
  const repaint = () => { const g = fg.current; if (g) g.nodeCanvasObject(g.nodeCanvasObject()); };

  /** The nodes in focus — the hovered one and its neighbours, else the highlighted ones — or null for all. */
  const focus = (): Set<string> | null => {
    const { hover, highlight: hl } = view.current;
    if (hover) return new Set([hover, ...(neighbours.current.get(hover) ?? [])]);
    return hl.size ? hl : null;
  };

  useEffect(() => {
    const el = container.current!;
    const g = new ForceGraph<Node, Link>(el)
      .backgroundColor(BG)
      .width(el.clientWidth)
      .height(el.clientHeight)
      .nodeId('id')
      .nodeLabel(() => '')
      .warmupTicks(40)
      .cooldownTime(12000)
      .minZoom(0.05)
      .maxZoom(12)
      .linkDirectionalArrowLength(0)
      .linkColor(link => {
        const f = focus();
        const on = f && f.has(endId(link.source)) && f.has(endId(link.target));
        if (on) return alpha(GOLD, 0.75);
        const base = link.valid ? 0.12 + link.weight * 0.22 : 0.08;
        return `rgba(190, 200, 215, ${f ? base * 0.25 : base})`;
      })
      .linkWidth(link => 0.35 + link.weight * 0.6)
      .linkLineDash(link => (link.valid ? null : [2, 2]))
      .nodeCanvasObjectMode(() => 'replace')
      .nodeCanvasObject((node, ctx, scale) => {
        const f = focus();
        const { selected: sel, highlight: hl } = view.current;
        const lit = !f || f.has(node.id);
        const r = radius(node);
        ctx.beginPath();
        ctx.arc(node.x!, node.y!, r, 0, 2 * Math.PI);
        ctx.fillStyle = alpha(node.color, lit ? (node.lone && !f ? 0.55 : 1) : 0.1);
        ctx.fill();
        if (node.id === sel || hl.has(node.id)) {
          ctx.lineWidth = 1.2 / Math.sqrt(scale);
          ctx.strokeStyle = node.id === sel ? '#ffffff' : GOLD;
          ctx.stroke();
        }
        // Names fade in with zoom; the node in focus and its neighbours are always named.
        const named = (f && f.has(node.id)) || node.id === sel;
        const fade = Math.min(1, Math.max(0, (scale - LABEL_FROM) / (LABEL_FULL - LABEL_FROM)));
        const a = named ? 1 : lit ? fade * (node.lone ? 0.6 : 1) : 0;
        if (a <= 0.02) return;
        const size = Math.max(10 / scale, 3.2);
        ctx.font = `${size}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = `rgba(${LABEL}, ${a})`;
        ctx.fillText(node.name, node.x!, node.y! + r + 1.5);
      })
      .nodePointerAreaPaint((node, color, ctx) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x!, node.y!, radius(node) + 2, 0, 2 * Math.PI);
        ctx.fill();
      })
      .onNodeHover(node => {
        view.current.hover = node?.id ?? null;
        el.style.cursor = node ? 'pointer' : '';
        repaint();
      })
      .onNodeClick(node => onSelectRef.current(node.id))
      .onBackgroundClick(() => onSelectRef.current(null))
      .onEngineStop(() => {
        if (!fitted.current) { fitted.current = true; g.zoomToFit(500, 40); }
      });
    // Obsidian's forces: repel, link pull, and a gentle pull to the centre that gathers the unlinked into a cloud.
    (g.d3Force('charge') as unknown as ForceManyBody<Node>).strength(-38).distanceMax(400);
    (g.d3Force('link') as unknown as ForceLink<Node, Link>).distance(26);
    g.d3Force('x', forceX<Node>(0).strength(0.06));
    g.d3Force('y', forceY<Node>(0).strength(0.06));
    fg.current = g;
    const observer = new ResizeObserver(() => g.width(el.clientWidth).height(el.clientHeight));
    observer.observe(el);
    return () => { observer.disconnect(); g._destructor(); fg.current = null; };
  }, []);

  // New data: keep every known node object (and so its place), add the new ones next to a neighbour.
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    const ids = new Set(graph.entities.map(e => e.uid));
    const drawn = graph.facts.filter(f => f.source_uid !== f.target_uid && ids.has(f.source_uid)
      && ids.has(f.target_uid) && (f.valid || showSuperseded));
    const near = new Map<string, Set<string>>();
    for (const f of drawn) {
      if (!near.has(f.source_uid)) near.set(f.source_uid, new Set());
      if (!near.has(f.target_uid)) near.set(f.target_uid, new Set());
      near.get(f.source_uid)!.add(f.target_uid);
      near.get(f.target_uid)!.add(f.source_uid);
    }
    neighbours.current = near;
    const known = nodes.current;
    const next = new Map<string, Node>();
    // Fresh nodes start scattered in a disc: d3's default spiral start leaves the unlinked ones (only weak forces
    // move them) in visible concentric rings.
    const disc = 12 * Math.sqrt(graph.entities.length);
    for (const e of graph.entities) {
      const degree = near.get(e.uid)?.size ?? 0;
      const node = known.get(e.uid) ?? ({ id: e.uid } as Node);
      Object.assign(node, {
        name: e.name.length > 28 ? e.name.slice(0, 27) + '…' : e.name,
        color: categoryColor(e.type, entityTypes),
        degree,
        lone: degree === 0,
      });
      if (node.x === undefined) {
        const anchor = [...(near.get(e.uid) ?? [])].map(id => known.get(id)).find(n => n?.x !== undefined);
        if (anchor) { node.x = anchor.x! + (Math.random() - 0.5) * 20; node.y = anchor.y! + (Math.random() - 0.5) * 20; }
        else {
          const r = disc * Math.sqrt(Math.random()), th = Math.random() * 2 * Math.PI;
          node.x = r * Math.cos(th);
          node.y = r * Math.sin(th);
        }
      }
      next.set(e.uid, node);
    }
    const changed = next.size !== known.size || [...next.keys()].some(id => !known.has(id))
      || drawn.length !== g.graphData().links.length;
    nodes.current = next;
    if (!changed) { repaint(); return; }
    if (known.size === 0) fitted.current = false;
    g.graphData({
      nodes: [...next.values()],
      links: drawn.map(f => ({ id: f.uid, source: f.source_uid, target: f.target_uid, weight: f.weight, valid: f.valid })),
    });
  }, [graph, entityTypes, showSuperseded]);

  // Highlight from the Head's answers: light them and bring them into view.
  useEffect(() => {
    view.current.highlight = highlight;
    repaint();
    if (highlight.size) fg.current?.zoomToFit(500, 80, n => highlight.has(n.id));
  }, [highlight]);

  useEffect(() => {
    view.current.selected = selected;
    repaint();
  }, [selected]);

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
              onClick={() => fg.current?.zoomToFit(400, 40)}
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
