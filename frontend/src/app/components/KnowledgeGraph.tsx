import { useCallback, useEffect, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  ConnectionLineType,
  Panel,
  type NodeProps,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { motion, AnimatePresence } from 'motion/react';
import { X } from 'lucide-react';
import { KG_API } from '../api';

/* ===== Types ===== */
interface GraphNode {
  id: string;
  label: string;
  category: string;
  reliability: number;
  tags: string[];
  source?: string;
  domain?: string;
}
interface GraphEdge {
  from: string;
  to: string;
  relation: string;
  weight: number;
}
interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  node_count?: number;
  edge_count?: number;
}

/* ===== Design tokens ===== */
const CAT_COLORS: Record<string, string> = {
  concept:            '#4fd1c5',
  technology:         '#68d391',
  person:             '#f0b86e',
  organization:       '#a78bfa',
  event:              '#fc8181',
  theory:             '#f6ad55',
  technical_indicator:'#4fd1c5',
  price_pattern:      '#f0b86e',
  risk_signal:        '#fc8181',
  strategy:           '#68d391',
  market_condition:   '#a78bfa',
  default:            '#4fd1c5',
};

const CAT_GLYPHS: Record<string, string> = {
  concept: '◆', technology: '⬡', person: '◉',
  organization: '▣', event: '◈', theory: '◇',
  default: '◆',
};

function catColor(c: string): string {
  for (const [k, v] of Object.entries(CAT_COLORS)) {
    if (c.toLowerCase().includes(k)) return v;
  }
  return CAT_COLORS.default;
}
function catGlyph(c: string): string {
  for (const [k, v] of Object.entries(CAT_GLYPHS)) {
    if (c.toLowerCase().includes(k)) return v;
  }
  return CAT_GLYPHS.default;
}
function relWord(r: number): string {
  if (r >= 0.85) return 'HIGH';
  if (r >= 0.60) return 'MED';
  return 'LOW';
}
function relClass(r: number): string {
  if (r >= 0.85) return 'high';
  if (r >= 0.60) return 'mid';
  return 'low';
}

/* ===== Custom Node ===== */
const REL_COLORS: Record<string, string> = { high: '#68d391', mid: '#f0b86e', low: '#fc8181' };

function SovereignNode({ data, selected }: NodeProps) {
  const node = data as GraphNode & { __color: string };
  const color = node.__color;
  const rc = relClass(node.reliability);
  const relColor = REL_COLORS[rc];

  return (
    <div
      style={{
        width: 188,
        padding: '10px 12px 8px',
        background: selected ? 'var(--surface-2)' : 'var(--surface)',
        border: `1px solid ${selected ? color : 'var(--border-2)'}`,
        borderLeft: `2px solid ${color}`,
        borderRadius: 3,
        boxShadow: selected
          ? `0 0 0 1px ${color}, 0 0 18px -4px ${color}40, 0 8px 22px rgba(0,0,0,0.55)`
          : '0 1px 0 rgba(0,0,0,0.4), 0 4px 14px rgba(0,0,0,0.35)',
        transition: 'all 160ms',
        cursor: 'pointer',
        fontFamily: 'var(--mono)',
      }}
    >
      {/* top row: glyph + category + reliability badge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span style={{ fontSize: 11, color, lineHeight: 1 }}>{catGlyph(node.category)}</span>
        <span style={{ flex: 1, fontSize: 9.5, letterSpacing: '0.14em', color: 'var(--text-dim)', textTransform: 'uppercase' }}>
          {node.category}
        </span>
        <span style={{
          fontSize: 10, fontWeight: 600, padding: '1px 4px', borderRadius: 2, letterSpacing: '0.04em',
          color: relColor, background: `${relColor}14`,
        }}>
          {relWord(node.reliability)}
        </span>
      </div>

      {/* label */}
      <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)', lineHeight: 1.25, marginBottom: 8, fontFamily: 'var(--sans)' }}>
        {node.label.length > 48 ? node.label.slice(0, 48) + '…' : node.label}
      </div>

      {/* tags */}
      {node.tags?.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
          {node.tags.slice(0, 3).map((t) => (
            <span key={t} style={{
              fontSize: 9.5, letterSpacing: '0.02em', color: 'var(--text-dim)',
              padding: '1px 5px', border: '1px solid var(--border-2)', borderRadius: 2,
              background: 'rgba(0,0,0,0.25)',
            }}>{t}</span>
          ))}
          {node.tags.length > 3 && (
            <span style={{
              fontSize: 9.5, color: 'var(--text-faint)', padding: '1px 5px',
              border: '1px dashed var(--border-2)', borderRadius: 2,
            }}>+{node.tags.length - 3}</span>
          )}
        </div>
      )}

      {/* reliability bar */}
      <div style={{ height: 2, background: 'var(--border)', borderRadius: 1, overflow: 'hidden' }}>
        <div style={{ width: `${node.reliability * 100}%`, height: '100%', background: color, opacity: 0.7 }} />
      </div>
    </div>
  );
}

/* ===== Node Detail Sidebar ===== */
function NodeDetail({ node, edges, allNodes, onClose, onNavigate }: {
  node: GraphNode;
  edges: GraphEdge[];
  allNodes: GraphNode[];
  onClose: () => void;
  onNavigate: (id: string) => void;
}) {
  const color = catColor(node.category);
  const rc = relClass(node.reliability);
  const relColor = REL_COLORS[rc];
  const connectedEdges = edges.filter(e => e.from === node.id || e.to === node.id);

  return (
    <AnimatePresence>
      <motion.div
        key={node.id}
        initial={{ x: 20, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 20, opacity: 0 }}
        transition={{ duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }}
        style={{
          position: 'absolute', top: 0, right: 0, height: '100%', width: 360,
          background: 'var(--surface)', borderLeft: '1px solid var(--border-2)',
          display: 'flex', flexDirection: 'column', zIndex: 10, fontFamily: 'var(--mono)',
        }}
      >
        {/* header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 16px', borderBottom: '1px solid var(--border)',
          background: 'linear-gradient(to bottom, #11151d, var(--surface))',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' }} />
            <span style={{ fontSize: 10, letterSpacing: '0.14em', color: 'var(--text-dim)', textTransform: 'uppercase' }}>
              {node.category}
            </span>
            <span style={{
              fontSize: 10, color: 'var(--text-faint)', padding: '1px 5px',
              border: '1px solid var(--border-2)', borderRadius: 2,
            }}>
              {node.id}
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              fontSize: 12, color: 'var(--text-faint)', width: 22, height: 22,
              borderRadius: 2, display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'none', border: 'none', cursor: 'pointer', transition: 'all 120ms',
            }}
            onMouseEnter={e => { (e.target as HTMLElement).style.color = 'var(--text)'; (e.target as HTMLElement).style.background = 'var(--surface-2)'; }}
            onMouseLeave={e => { (e.target as HTMLElement).style.color = 'var(--text-faint)'; (e.target as HTMLElement).style.background = 'none'; }}
          >
            <X size={12} />
          </button>
        </div>

        {/* body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 18px 24px', scrollbarWidth: 'thin', scrollbarColor: 'var(--border-3) transparent' }}>
          <div style={{ fontSize: 20, fontWeight: 500, marginBottom: 20, lineHeight: 1.2, fontFamily: 'var(--sans)', color: 'var(--text)' }}>
            {node.label}
          </div>

          {/* reliability */}
          <div style={{ marginBottom: 22 }}>
            <div style={{ fontSize: 10, letterSpacing: '0.16em', color: 'var(--text-faint)', marginBottom: 8, textTransform: 'uppercase' }}>
              Reliability
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ flex: 1, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${node.reliability * 100}%` }}
                  transition={{ duration: 0.5, ease: 'easeOut' }}
                  style={{ height: '100%', background: relColor, borderRadius: 2 }}
                />
              </div>
              <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)', minWidth: 36, textAlign: 'right' }}>
                {Math.round(node.reliability * 100)}%
              </span>
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--text-dim)', marginTop: 5, letterSpacing: '0.04em' }}>
              {relWord(node.reliability)} confidence
            </div>
          </div>

          {/* tags */}
          {node.tags?.length > 0 && (
            <div style={{ marginBottom: 22 }}>
              <div style={{ fontSize: 10, letterSpacing: '0.16em', color: 'var(--text-faint)', marginBottom: 8, textTransform: 'uppercase' }}>
                Tags
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {node.tags.map(t => (
                  <span key={t} style={{
                    fontSize: 10.5, padding: '2px 7px', border: '1px solid var(--border-2)',
                    borderRadius: 2, color: 'var(--text-dim)', background: 'rgba(0,0,0,0.2)',
                  }}>{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* connections */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 10, letterSpacing: '0.16em', color: 'var(--text-faint)', textTransform: 'uppercase' }}>
                Connections
              </span>
              <span style={{
                fontSize: 9.5, padding: '1px 5px', border: '1px solid var(--border-2)',
                borderRadius: 2, color: 'var(--text-dim)',
              }}>{connectedEdges.length}</span>
            </div>
            {connectedEdges.length === 0 && (
              <p style={{ fontSize: 10.5, color: 'var(--text-faint)', padding: '4px 0' }}>No connections</p>
            )}
            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
              {connectedEdges.map((e, i) => {
                const otherId = e.from === node.id ? e.to : e.from;
                const other = allNodes.find(n => n.id === otherId);
                const otherColor = other ? catColor(other.category) : 'var(--text-dim)';
                const direction = e.from === node.id ? '→' : '←';
                return (
                  <li
                    key={i}
                    onClick={() => onNavigate(otherId)}
                    style={{
                      display: 'grid', gridTemplateColumns: '1fr auto', gap: '4px 10px',
                      padding: '8px 10px', border: '1px solid transparent', borderRadius: 2,
                      cursor: 'pointer', transition: 'all 100ms', alignItems: 'center',
                    }}
                    onMouseEnter={e2 => { (e2.currentTarget as HTMLElement).style.background = 'var(--surface-2)'; (e2.currentTarget as HTMLElement).style.borderColor = 'var(--border-2)'; }}
                    onMouseLeave={e2 => { (e2.currentTarget as HTMLElement).style.background = 'transparent'; (e2.currentTarget as HTMLElement).style.borderColor = 'transparent'; }}
                  >
                    <span style={{ fontSize: 10, letterSpacing: '0.06em', color: 'var(--text-dim)', textTransform: 'uppercase', gridColumn: 1 }}>
                      <span style={{ color: 'var(--text-faint)', marginRight: 4 }}>{direction}</span>
                      {e.relation}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--accent)', gridColumn: 2, gridRow: '1 / span 2', alignSelf: 'center' }}>
                      {e.weight.toFixed(2)}
                    </span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, gridColumn: 1 }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: otherColor, flexShrink: 0, display: 'inline-block' }} />
                      <span style={{ fontSize: 13, color: 'var(--text)', fontFamily: 'var(--sans)' }}>
                        {other?.label ?? otherId}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}

/* ===== Node/Edge builders ===== */
const nodeTypes = { sovereign: SovereignNode };

function buildNodes(gNodes: GraphNode[]): Node[] {
  const cols = Math.ceil(Math.sqrt(gNodes.length));
  return gNodes.map((n, i) => ({
    id: n.id,
    type: 'sovereign',
    position: { x: (i % cols) * 240 + 60, y: Math.floor(i / cols) * 200 + 60 },
    data: { ...n, __color: catColor(n.category) },
  }));
}

function buildEdges(gEdges: GraphEdge[]): Edge[] {
  return gEdges.map((e, i) => ({
    id: `e-${i}`,
    source: e.from,
    target: e.to,
    type: 'straight',
    animated: e.weight > 0.75,
    style: {
      stroke: e.weight > 0.75 ? 'rgba(79,209,197,0.5)' : 'rgba(42,51,68,0.8)',
      strokeWidth: e.weight > 0.75 ? 1.5 : 1,
    },
    label: e.relation,
    labelStyle: {
      fill: '#f0b86e', fontSize: 9, fontFamily: '"JetBrains Mono", monospace',
      letterSpacing: '0.06em', textTransform: 'uppercase' as const,
    },
    labelBgStyle: { fill: '#0e1117', fillOpacity: 1 },
    labelBgPadding: [4, 6] as [number, number],
    labelBgBorderRadius: 2,
  }));
}

/* ===== Main component ===== */

export function KnowledgeGraph({ domain }: { domain: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    setIsLoading(true);
    setSelectedNode(null);

    fetch(`${KG_API}/graph/${encodeURIComponent(domain)}`)
      .then(r => r.json())
      .then((data: GraphData) => {
        setGraphData(data);
        setNodes(buildNodes(data.nodes));
        setEdges(buildEdges(data.edges));
        setIsLoading(false);
      })
      .catch(() => setIsLoading(false));
  }, [domain]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    const raw = graphData?.nodes.find(n => n.id === node.id);
    if (raw) setSelectedNode(raw);
  }, [graphData]);

  const navigateToNode = useCallback((id: string) => {
    const raw = graphData?.nodes.find(n => n.id === id);
    if (raw) setSelectedNode(raw);
  }, [graphData]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {isLoading && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', background: 'var(--bg-2)', zIndex: 5,
        }}>
          <div style={{
            width: 32, height: 32, border: '1.5px solid var(--border-2)',
            borderTopColor: 'var(--accent)', borderRadius: '50%',
            animation: 'spin 0.9s linear infinite',
          }} />
          <div style={{
            marginTop: 14, fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.12em',
            color: 'var(--text-faint)', textTransform: 'uppercase',
          }}>
            Loading graph
          </div>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        nodeTypes={nodeTypes}
        connectionLineType={ConnectionLineType.Straight}
        fitView
        proOptions={{ hideAttribution: true }}
        style={{ background: 'var(--bg-2)' }}
      >
        <Background color="rgba(255,255,255,0.025)" gap={32} size={1} />
        <Controls className="rf-controls-dark" />
        <MiniMap
          nodeColor={n => catColor((graphData?.nodes.find(gn => gn.id === n.id)?.category) ?? '')}
          maskColor="rgba(7,9,12,0.75)"
          style={{ background: 'rgba(14,17,23,0.92)', border: '1px solid var(--border)', borderRadius: 3 }}
        />
        <Panel position="top-left">
          <div style={{
            background: 'rgba(14,17,23,0.88)', border: '1px solid var(--border)',
            borderRadius: 3, padding: '10px 12px', backdropFilter: 'blur(8px)',
            fontFamily: 'var(--mono)',
          }}>
            {[
              { k: 'NODES', v: graphData?.nodes.length ?? 0 },
              { k: 'EDGES', v: graphData?.edges.length ?? 0 },
            ].map(({ k, v }) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 20, marginBottom: 4 }}>
                <span style={{ fontSize: 10, letterSpacing: '0.16em', color: 'var(--text-faint)', textTransform: 'uppercase' }}>{k}</span>
                <span style={{ fontSize: 11, color: 'var(--text)' }}>{v}</span>
              </div>
            ))}
          </div>
        </Panel>
      </ReactFlow>

      {selectedNode && (
        <NodeDetail
          node={selectedNode}
          edges={graphData?.edges ?? []}
          allNodes={graphData?.nodes ?? []}
          onClose={() => setSelectedNode(null)}
          onNavigate={navigateToNode}
        />
      )}
    </div>
  );
}
