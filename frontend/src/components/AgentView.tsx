import { useEffect, useRef, useState } from 'react';
import {
  Code2, Crown, Eye, FileText, GitMerge, Globe, Link2, Newspaper, Radar, ScanText, Shapes, ShieldCheck, Target, User,
  type LucideIcon,
} from 'lucide-react';
import { kg, research, type ActivityEvent, type TeamAgent } from '../lib/api';

/**
 * The main view: the Head, the knowledge graph and the team, as the user drew it — the Head on top, the graph at the
 * centre, the agents on a dial around it. Drawn in graphite: an agent at work turns bone-white and its ring turns;
 * every hand-off (a task, a report, a tool call, facts written to the graph) travels its line as a comet, its kind
 * told by the comet's shape. Below, the console lists the hand-offs. Polls the KG's activity feed.
 */

const W = 1000, H = 640;
const HEAD = { x: 500, y: 76 }, USER = { x: 850, y: 76 }, CORE = { x: 500, y: 262, r: 74 };
const ARC = { rx: 432, ry: 318 };   // the team's dial, centred on the graph: every line to it is a clear spoke
const NODE_R = 21;
const TEAMS: { label: string; agents: string[]; caption?: number }[] = [
  // caption: where along its span a team is named (default the middle); Learning's steep end holds agents' status
  { label: 'Learning', agents: ['research', 'extractor', 'validator', 'resolver', 'linker', 'ontologist'], caption: 0.75 },
  { label: 'Live data', agents: ['scout', 'coder'] },
  { label: 'Decisions', agents: ['strategist', 'watcher'] },
  { label: 'Briefing', agents: ['summarizer', 'reporter'] },
];
const ICON: Record<string, LucideIcon> = {
  head: Crown, user: User, research: Globe, extractor: ScanText, validator: ShieldCheck, resolver: GitMerge,
  linker: Link2, ontologist: Shapes, scout: Radar, coder: Code2, strategist: Target, watcher: Eye,
  summarizer: FileText, reporter: Newspaper,
};
// The graphite scale (index.css) — SVG attributes take no CSS variables.
const C = { bg: '#0a0a0a', node: '#101010', line: '#1e1e1e', line2: '#2e2e2e', edge: '#3a3a3a',
            faint: '#6b6b6b', dim: '#a3a3a3', ink: '#e6e6e6', accent: '#f2efe8' };
type Glyph = 'dot' | 'ring' | 'square' | 'diamond';
const GLYPH: Record<string, Glyph> = {
  task: 'dot', question: 'dot', note: 'dot', report: 'ring', answer: 'ring', notice: 'ring', tool: 'square',
  write: 'diamond',
};
const LEGEND: [Glyph, string][] = [['dot', 'task · question'], ['ring', 'report · answer'], ['square', 'tool call'], ['diamond', 'facts written']];
const FLIGHT_MS = 1500;
const MONO = '"Geist Mono Variable", ui-monospace, monospace';
const rad = (deg: number) => (deg * Math.PI) / 180;
const on = (a: number, r = 0) => ({ x: CORE.x + (ARC.rx + r) * Math.cos(a), y: CORE.y + (ARC.ry + r) * Math.sin(a) });

/** Agent angles along the dial, left to right, a gap between teams; and each team's span. */
function layout() {
  const angle: Record<string, number> = {};
  const spans: { label: string; from: number; to: number; caption: number }[] = [];
  const slots = TEAMS.reduce((n, t) => n + t.agents.length, 0) - 1 + (TEAMS.length - 1) * 0.6;
  const at = (s: number) => rad(172 - (s / slots) * 164);    // 172° → 8°: below the graph
  let s = 0;
  for (const team of TEAMS) {
    const first = s;
    for (const name of team.agents) angle[name] = at(s++);
    spans.push({ label: team.label, from: at(first - 0.3), to: at(s - 1 + 0.3), caption: team.caption ?? 0.5 });
    s += 0.6;
  }
  return { angle, spans };
}
const { angle: ANGLE, spans: SPANS } = layout();
const POS = Object.fromEntries(Object.entries(ANGLE).map(([n, a]) => [n, on(a)]));

/** A small fixed constellation inside the graph's core. */
const STARS = (() => {
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const pts = Array.from({ length: 18 }, () => {
    const a = rnd() * Math.PI * 2, r = 12 + Math.sqrt(rnd()) * 44;
    return { x: CORE.x + r * Math.cos(a), y: CORE.y + r * Math.sin(a) };
  });
  const edges: [number, number][] = [];
  pts.forEach((p, i) => {
    pts.map((q, j) => ({ j, d: Math.hypot(p.x - q.x, p.y - q.y) })).filter(o => o.j !== i)
      .sort((a, b) => a.d - b.d).slice(0, 2).forEach(({ j }) => { if (!edges.some(([a, b]) => a === j && b === i)) edges.push([i, j]); });
  });
  return { pts, edges };
})();

function point(name: string) {
  if (name === 'head') return HEAD;
  if (name === 'user') return USER;
  if (name === 'graph') return CORE;
  return POS[name] ?? CORE;
}

/** The line a hand-off travels: Head ↔ agent bends around the graph on the agent's side; the rest go straight. */
function path(from: string, to: string): string {
  const a = point(from), b = point(to);
  const agent = from === 'head' && POS[to] ? b : to === 'head' && POS[from] ? a : null;
  if (agent) {
    const cx = agent.x < CORE.x ? CORE.x - CORE.r - 150 : CORE.x + CORE.r + 150;
    return `M ${a.x} ${a.y} Q ${cx} ${CORE.y - 30} ${b.x} ${b.y}`;
  }
  return `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
}

/** The drawing's loops: work goes down the left to the team, reports come up the right — above the Head's labels. */
const LOOP_TASKS = `M ${HEAD.x - 44} ${HEAD.y - 30} C 150 ${HEAD.y - 40}, 26 ${CORE.y - 70}, ${POS.research.x - 14} ${POS.research.y - 30}`;
const LOOP_REPORTS = `M ${POS.reporter.x + 14} ${POS.reporter.y - 30} C ${W - 26} ${CORE.y - 70}, ${W - 150} ${HEAD.y - 40}, ${HEAD.x + 44} ${HEAD.y - 30}`;

/** Where an agent's current work is written: under it, or — where the dial is steep — inside the dial beside it. */
function doingAt(name: string) {
  const a = ANGLE[name], p = POS[name];
  if (a > rad(140)) return { x: p.x + NODE_R + 10, y: p.y + 4, anchor: 'start' as const };
  if (a < rad(40)) return { x: p.x - NODE_R - 10, y: p.y + 4, anchor: 'end' as const };
  return { x: p.x, y: p.y + NODE_R + 30, anchor: 'middle' as const };
}

/** A spoke from an agent's rim to the graph's rim. */
function spoke(name: string) {
  const p = POS[name], dx = CORE.x - p.x, dy = CORE.y - p.y, d = Math.hypot(dx, dy);
  return { x1: p.x + (dx / d) * (NODE_R + 4), y1: p.y + (dy / d) * (NODE_R + 4),
           x2: CORE.x - (dx / d) * (CORE.r + 6), y2: CORE.y - (dy / d) * (CORE.r + 6) };
}

/** An arc of the dial at radius offset `r`, from angle `a` to `b` (a > b: left to right along the bottom). */
function arc(a: number, b: number, r: number) {
  const p = on(a, r), q = on(b, r);
  return `M ${p.x} ${p.y} A ${ARC.rx + r} ${ARC.ry + r} 0 ${a - b > Math.PI ? 1 : 0} 0 ${q.x} ${q.y}`;
}

function Mark({ glyph, x = 0, y = 0, size = 4.5, color = C.accent }: { glyph: Glyph; x?: number; y?: number; size?: number; color?: string }) {
  if (glyph === 'dot') return <circle cx={x} cy={y} r={size} fill={color} />;
  if (glyph === 'ring') return <circle cx={x} cy={y} r={size} fill={C.bg} stroke={color} strokeWidth={1.6} />;
  if (glyph === 'square') return <rect x={x - size * 0.85} y={y - size * 0.85} width={size * 1.7} height={size * 1.7} fill={color} />;
  return <rect x={x - size * 0.8} y={y - size * 0.8} width={size * 1.6} height={size * 1.6} fill={color} transform={`rotate(45 ${x} ${y})`} />;
}

function GlyphIcon({ glyph }: { glyph: Glyph }) {
  return <svg width={10} height={10} viewBox="-5 -5 10 10" className="shrink-0"><Mark glyph={glyph} size={3.4} /></svg>;
}

interface Flight { id: number; d: string; glyph: Glyph }

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour12: false });
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export function AgentView({ domain, stats }: { domain: string; stats?: { entities: number; facts: number } }) {
  const [agents, setAgents] = useState<TeamAgent[]>([]);
  const [working, setWorking] = useState<Record<string, string[]>>({});
  const [log, setLog] = useState<ActivityEvent[]>([]);
  const [flights, setFlights] = useState<Flight[]>([]);
  const [recent, setRecent] = useState<Record<string, number>>({});   // agent → last hand-off time
  const [, tick] = useState(0);
  const after = useRef(0);
  const flightId = useRef(0);

  useEffect(() => {
    let alive = true;
    after.current = 0;
    const poll = async () => {
      try {
        const [a, jobs] = await Promise.all([kg.activity(domain, after.current), research.jobs(domain).catch(() => [])]);
        if (!alive) return;
        const busy: Record<string, string[]> = {};
        for (const w of a.working) busy[w.agent] = w.doing;
        const run = jobs.find(j => ['queued', 'researching', 'ontology'].includes(j.status));
        if (run) busy.research = [run.question || `${run.mode} — ${run.status}`];
        setWorking(busy);
        const fresh = after.current === 0 ? [] : a.events;   // the first poll only sets the cursor
        after.current = a.seq;
        tick(t => t + 1);
        if (fresh.length) {
          setLog(l => [...fresh.slice().reverse(), ...l].slice(0, 40));
          const now = Date.now();
          setRecent(r => {
            const next = { ...r };
            for (const e of fresh) { next[e.source] = now; next[e.target] = now; }
            return next;
          });
          const added = fresh.slice(-6).map(e => ({ id: ++flightId.current, d: path(e.source, e.target), glyph: GLYPH[e.kind] ?? 'dot' }));
          setFlights(f => [...f, ...added]);
          setTimeout(() => setFlights(f => f.filter(x => !added.includes(x))), FLIGHT_MS + 100);
        }
      } catch { /* the next poll tries again */ }
    };
    void poll();
    const t = setInterval(poll, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [domain]);

  useEffect(() => {
    let alive = true;
    const load = () => kg.team(domain).then(t => alive && setAgents(t.agents)).catch(() => {});
    void load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [domain]);

  const title = (name: string) => agents.find(a => a.name === name)?.title ?? (name === 'head' ? 'Head' : name === 'user' ? 'You' : name === 'graph' ? 'Graph' : name);
  const active = (name: string) => !!working[name] || (recent[name] ?? 0) > Date.now() - 6000;
  const queued = Object.fromEntries(agents.map(a => [a.name, a.queued]));
  const busyCount = Object.keys(working).filter(n => n !== 'graph').length;
  const queuedCount = agents.reduce((n, a) => n + a.queued, 0);
  const perMinute = log.filter(e => Date.now() - new Date(e.at).getTime() < 60_000).length;
  const live = busyCount > 0 || perMinute > 0;
  const headOn = active('head'), graphOn = active('graph');
  const HeadIcon = ICON.head, UserIcon = ICON.user;

  return (
    <div className="flex h-full w-full flex-col bg-bg">
      <div className="relative min-h-0 flex-1">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="h-full w-full select-none">
          <defs>
            <pattern id="grid" width={20} height={20} patternUnits="userSpaceOnUse">
              <circle cx={1} cy={1} r={0.7} fill="#ffffff" fillOpacity={0.06} />
            </pattern>
            <radialGradient id="core" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#ffffff" stopOpacity={graphOn ? 0.1 : 0.05} />
              <stop offset="100%" stopColor="#ffffff" stopOpacity={0.01} />
            </radialGradient>
            <radialGradient id="vignette" cx="50%" cy="42%" r="65%">
              <stop offset="60%" stopColor={C.bg} stopOpacity={0} />
              <stop offset="100%" stopColor={C.bg} stopOpacity={0.9} />
            </radialGradient>
            <filter id="glow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="5" result="b" />
              <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <marker id="tip" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M2 1L8 5L2 9" fill="none" stroke={C.faint} strokeWidth={1.4} />
            </marker>
            <path id="core-caption" d={`M ${CORE.x - CORE.r - 13} ${CORE.y} A ${CORE.r + 13} ${CORE.r + 13} 0 0 0 ${CORE.x + CORE.r + 13} ${CORE.y}`} />
          </defs>
          <rect width={W} height={H} fill="url(#grid)" />
          <rect width={W} height={H} fill="url(#vignette)" />

          {/* the dial the team sits on, its teams bracketed inside it */}
          <path d={arc(rad(176), rad(4), 0)} stroke={C.line} strokeWidth={1} fill="none" />
          {Array.from({ length: 43 }, (_, i) => rad(176 - i * 4)).map((a, i) => {
            const p = on(a, -3), q = on(a, i % 5 === 0 ? 6 : 3);
            return <line key={i} x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={C.line2} strokeWidth={1} />;
          })}
          {SPANS.map(s => {
            const l = on(s.from + (s.to - s.from) * s.caption, -62);
            const cap = (a: number) => { const p = on(a, -40), q = on(a, -47); return <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={C.edge} />; };
            return (
              <g key={s.label}>
                <path d={arc(s.from, s.to, -40)} stroke={C.edge} strokeWidth={1} fill="none" />
                {cap(s.from)}{cap(s.to)}
                <text x={l.x} y={l.y + 3} textAnchor="middle" fill={C.faint} fontSize={9} letterSpacing={2.2}
                  fontFamily={MONO} stroke={C.bg} strokeWidth={4} paintOrder="stroke">{s.label.toUpperCase()}</text>
              </g>
            );
          })}

          {/* spokes: every agent writes to the graph; a busy agent's spoke flows */}
          {Object.keys(POS).map(n => {
            const s = spoke(n), lit = active(n);
            return <line key={n} {...s} stroke={lit ? C.accent : C.line} strokeOpacity={lit ? 0.45 : 1} strokeWidth={1}
              className={lit ? 'flow' : undefined} />;
          })}

          {/* the drawing's loops: the Head gives work down the left, the team reports back up the right */}
          <path d={LOOP_TASKS} stroke={C.edge} strokeWidth={1} strokeDasharray="3 5" fill="none" markerEnd="url(#tip)" />
          <path d={LOOP_REPORTS} stroke={C.edge} strokeWidth={1} strokeDasharray="3 5" fill="none" markerEnd="url(#tip)" />
          <text x={96} y={172} fill={C.faint} fontSize={9} letterSpacing={2.4} fontFamily={MONO}>TASKS · QUESTIONS ↓</text>
          <text x={W - 96} y={172} textAnchor="end" fill={C.faint} fontSize={9} letterSpacing={2.4} fontFamily={MONO}>↑ REPORTS · ANSWERS</text>

          {/* you ↔ Head ↔ graph */}
          <line x1={USER.x - 18} y1={USER.y - 7} x2={HEAD.x + 44} y2={HEAD.y - 32} stroke={C.edge} strokeDasharray="2 4" />
          <line x1={HEAD.x} y1={HEAD.y + 56} x2={CORE.x} y2={CORE.y - CORE.r - 20} stroke={headOn || graphOn ? C.accent : C.line2}
            strokeOpacity={headOn || graphOn ? 0.5 : 1} className={headOn ? 'flow' : undefined} />

          {/* hand-offs in flight: the line lights, a comet runs it */}
          {flights.map(f => (
            <g key={f.id}>
              <path d={f.d} stroke={C.accent} strokeOpacity={0.12} strokeWidth={1} fill="none" />
              <path d={f.d} pathLength={1} stroke={C.accent} strokeWidth={1.6} strokeLinecap="round" fill="none" strokeDasharray="0.14 2">
                <animate attributeName="stroke-dashoffset" from="0.14" to="-1" dur={`${FLIGHT_MS}ms`} fill="freeze" />
              </path>
            </g>
          ))}

          {/* the knowledge graph */}
          <g>
            <circle cx={CORE.x} cy={CORE.y} r={CORE.r + 22} fill="none" stroke={C.accent} strokeOpacity={graphOn ? 0.35 : 0.12}
              strokeDasharray="1 7" className="spin-slower" />
            <circle cx={CORE.x} cy={CORE.y} r={CORE.r} fill="url(#core)" stroke={graphOn ? C.accent : C.line2}
              strokeOpacity={graphOn ? 0.8 : 1} filter={graphOn ? 'url(#glow)' : undefined} />
            {STARS.edges.map(([i, j], k) => (
              <line key={k} x1={STARS.pts[i].x} y1={STARS.pts[i].y} x2={STARS.pts[j].x} y2={STARS.pts[j].y}
                stroke={C.accent} strokeOpacity={graphOn ? 0.22 : 0.09} />
            ))}
            {STARS.pts.map((p, k) => <circle key={k} cx={p.x} cy={p.y} r={1.5} fill={C.accent} fillOpacity={graphOn ? 0.6 : 0.25} />)}
            <text x={CORE.x} y={CORE.y + 2} textAnchor="middle" fill={C.accent} fontSize={26} fontWeight={300}
              fontFamily={MONO} stroke={C.bg} strokeWidth={5} paintOrder="stroke" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {stats ? stats.entities.toLocaleString() : '—'}
            </text>
            <text x={CORE.x} y={CORE.y + 18} textAnchor="middle" fill={C.dim} fontSize={8.5} letterSpacing={2}
              fontFamily={MONO} stroke={C.bg} strokeWidth={4} paintOrder="stroke">ENTITIES</text>
            <text x={CORE.x} y={CORE.y + 34} textAnchor="middle" fill={C.faint} fontSize={9.5}
              fontFamily={MONO} stroke={C.bg} strokeWidth={4} paintOrder="stroke">
              {stats ? `${stats.facts.toLocaleString()} facts` : ''}
            </text>
            <text fontSize={9} letterSpacing={3} fill={graphOn ? C.accent : C.dim} fontFamily={MONO} dy={4}
              stroke={C.bg} strokeWidth={4} paintOrder="stroke">
              <textPath href="#core-caption" startOffset="50%" textAnchor="middle">KNOWLEDGE GRAPH</textPath>
            </text>
          </g>

          {/* you */}
          <g>
            <circle cx={USER.x} cy={USER.y} r={17} fill={C.node} stroke={C.edge} />
            <UserIcon x={USER.x - 7} y={USER.y - 7} width={14} height={14} color={C.dim} strokeWidth={1.6} />
            <text x={USER.x} y={USER.y + 34} textAnchor="middle" fill={C.faint} fontSize={9} letterSpacing={2} fontFamily={MONO}>YOU</text>
          </g>

          {/* the Head: a dial that turns while it works */}
          <g>
            <g className={headOn ? 'spin-slow' : undefined}>
              {Array.from({ length: 72 }, (_, i) => {
                const a = rad(i * 5), r2 = i % 6 === 0 ? 53 : 50;
                return <line key={i} x1={HEAD.x + 46 * Math.cos(a)} y1={HEAD.y + 46 * Math.sin(a)} x2={HEAD.x + r2 * Math.cos(a)} y2={HEAD.y + r2 * Math.sin(a)}
                  stroke={headOn ? C.accent : C.edge} strokeOpacity={headOn ? 0.7 : 1} />;
              })}
            </g>
            <circle cx={HEAD.x} cy={HEAD.y} r={33} fill={headOn ? C.accent : C.node} stroke={headOn ? C.accent : C.edge}
              filter={headOn ? 'url(#glow)' : undefined} />
            <HeadIcon x={HEAD.x - 11} y={HEAD.y - 11} width={22} height={22} color={headOn ? C.bg : C.ink} strokeWidth={1.5} />
            <text x={HEAD.x - 68} y={HEAD.y - 1} textAnchor="end" fill={C.ink} fontSize={13} letterSpacing={3.5} fontFamily={MONO}>HEAD</text>
            <text x={HEAD.x - 68} y={HEAD.y + 14} textAnchor="end" fill={C.faint} fontSize={10}>orchestrator</text>
            <text x={HEAD.x + 68} y={HEAD.y - 1} fill={headOn ? C.accent : C.faint} fontSize={9} letterSpacing={2} fontFamily={MONO}>
              {headOn ? 'WORKING' : 'STANDING BY'}
            </text>
            {working.head?.[0] && <text x={HEAD.x + 68} y={HEAD.y + 14} fill={C.dim} fontSize={10.5}>{clip(working.head[0], 34)}</text>}
          </g>

          {/* the team */}
          {Object.entries(POS).map(([name, p]) => {
            const lit = active(name), doing = working[name]?.[0], waiting = queued[name] ?? 0;
            const Icon = ICON[name] ?? Shapes;
            return (
              <g key={name}>
                {lit && <circle cx={p.x} cy={p.y} r={NODE_R + 7} fill="none" stroke={C.accent} strokeOpacity={0.55}
                  strokeDasharray="2 5" className="spin-slow" />}
                {!lit && waiting > 0 && <circle cx={p.x} cy={p.y} r={NODE_R + 6} fill="none" stroke={C.faint} strokeDasharray="1 4" />}
                <circle cx={p.x} cy={p.y} r={NODE_R} fill={lit ? C.accent : C.node} stroke={lit ? C.accent : C.line2}
                  filter={lit ? 'url(#glow)' : undefined} />
                <Icon x={p.x - 8} y={p.y - 8} width={16} height={16} color={lit ? C.bg : C.dim} strokeWidth={1.6} />
                <text x={p.x} y={p.y + NODE_R + 16} textAnchor="middle" fill={lit ? C.accent : C.dim} fontSize={9.5}
                  letterSpacing={1.6} fontFamily={MONO} stroke={C.bg} strokeWidth={4} paintOrder="stroke">
                  {title(name).toUpperCase()}
                </text>
                {doing && (() => {
                  const d = doingAt(name);
                  return <text x={d.x} y={d.y} textAnchor={d.anchor} fill={C.dim} fontSize={10}
                    stroke={C.bg} strokeWidth={4} paintOrder="stroke">{clip(doing, 24)}</text>;
                })()}
                {waiting > 0 && (
                  <g>
                    <rect x={p.x + 12} y={p.y - NODE_R - 6} width={String(waiting).length * 6 + 9} height={13} rx={6.5} fill={C.ink} />
                    <text x={p.x + 12 + (String(waiting).length * 6 + 9) / 2} y={p.y - NODE_R + 4} textAnchor="middle" fill={C.bg}
                      fontSize={9} fontWeight={600} fontFamily={MONO}>{waiting}</text>
                  </g>
                )}
              </g>
            );
          })}

          {/* the comets' heads: their shape says what was handed over */}
          {flights.map(f => (
            <g key={`h${f.id}`} filter="url(#glow)">
              <Mark glyph={f.glyph} size={4.2} />
              <animateMotion dur={`${FLIGHT_MS}ms`} path={f.d} fill="freeze" />
            </g>
          ))}
        </svg>

        <div className="pointer-events-none absolute bottom-3 left-4 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[9.5px] tracking-wider text-faint uppercase">
          {LEGEND.map(([g, l]) => <span key={g} className="flex items-center gap-1.5"><GlyphIcon glyph={g} />{l}</span>)}
        </div>
      </div>

      {/* the console: every hand-off, newest first */}
      <div className="h-[148px] shrink-0 border-t border-line bg-panel/60">
        <div className="flex items-center gap-4 border-b border-line px-4 py-1.5 font-mono text-[9.5px] tracking-[0.18em] text-faint uppercase">
          <span className="flex items-center gap-2 text-dim">
            <span className={`h-1.5 w-1.5 rounded-full ${live ? 'pulse-dot bg-accent' : 'bg-faint'}`} />
            {live ? 'Live' : 'Idle'} · Transmissions
          </span>
          <span className="ml-auto">Working <b className="font-medium text-ink">{busyCount}</b></span>
          <span>Queued <b className="font-medium text-ink">{queuedCount}</b></span>
          <span>Per min <b className="font-medium text-ink">{perMinute}</b></span>
        </div>
        <div className="h-[calc(100%-27px)] overflow-hidden px-4 py-1">
          {log.length === 0 && <p className="py-2 font-mono text-[10.5px] text-faint">No hand-offs yet — the crew is standing by.</p>}
          {log.map((e, i) => (
            <div key={e.seq} className="flex items-center gap-3 py-[3px] font-mono text-[10.5px]" style={{ opacity: Math.max(0.3, 1 - i * 0.13) }}>
              <span className="w-[54px] shrink-0 text-faint tabular-nums">{clock(e.at)}</span>
              <GlyphIcon glyph={GLYPH[e.kind] ?? 'dot'} />
              <span className="shrink-0 tracking-wide text-ink uppercase">
                {title(e.source)} <span className="text-faint">→</span> {title(e.target)}
              </span>
              <span className="shrink-0 text-faint">{e.kind}</span>
              <span className="min-w-0 truncate font-sans text-dim">{e.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
