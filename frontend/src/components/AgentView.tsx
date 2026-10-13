import { useEffect, useRef, useState } from 'react';
import { kg, research, type ActivityEvent, type TeamAgent } from '../lib/api';

/**
 * The main view: the Head, the knowledge graph and the team, as the user drew it — the Head on top, the graph in the
 * middle, the agents below. An agent at work glows and says what it is doing; every hand-off (a task, a report, a
 * tool call, facts written to the graph) travels as a dot along its line. Polls the KG's activity feed.
 */

const W = 1000, H = 700;
const HEAD = { x: 500, y: 70 }, USER = { x: 860, y: 70 }, GRAPH = { x: 500, y: 250, w: 280, h: 84 };
const ARC = { rx: 448, ry: 372 };   // the team sits on an arc under the graph: every line to it is a clear spoke
const TEAMS: { label: string; agents: string[] }[] = [
  { label: 'Learning', agents: ['research', 'extractor', 'validator', 'resolver', 'linker', 'ontologist'] },
  { label: 'Live data', agents: ['scout', 'coder'] },
  { label: 'Decisions', agents: ['strategist', 'watcher'] },
  { label: 'Briefing', agents: ['summarizer', 'reporter'] },
];
const KIND_COLOR: Record<string, string> = {
  task: '#e2b356', question: '#e2b356', note: '#e2b356',
  report: '#4ade80', answer: '#4ade80', notice: '#4ade80',
  tool: '#5fb3f9', write: '#a78bfa',
};
const FLIGHT_MS = 1400;

/** Agent positions along the arc, left to right, a half-step gap between teams; and each team's label angle. */
function layout() {
  const pos: Record<string, { x: number; y: number }> = {};
  const labels: { label: string; x: number; y: number }[] = [];
  const slots = TEAMS.reduce((n, t) => n + t.agents.length, 0) - 1 + (TEAMS.length - 1) * 0.6;
  const at = (s: number) => (172 - (s / slots) * 164) * Math.PI / 180;     // 172° → 8°: below the graph
  let s = 0;
  for (const team of TEAMS) {
    const first = s;
    for (const name of team.agents) {
      const a = at(s);
      pos[name] = { x: GRAPH.x + ARC.rx * Math.cos(a), y: GRAPH.y + ARC.ry * Math.sin(a) };
      s += 1;
    }
    const mid = at((first + s - 1) / 2);
    labels.push({ label: team.label, x: Math.min(W - 60, Math.max(60, GRAPH.x + (ARC.rx - 110) * Math.cos(mid))),
                  y: GRAPH.y + (ARC.ry - 80) * Math.sin(mid) });   // inside the arc: never off the edge
    s += 0.6;
  }
  return { pos, labels };
}
const { pos: POS, labels: TEAM_LABELS } = layout();

function point(name: string) {
  if (name === 'head') return HEAD;
  if (name === 'user') return USER;
  if (name === 'graph') return { x: GRAPH.x, y: GRAPH.y };
  return POS[name] ?? { x: GRAPH.x, y: GRAPH.y };
}

/** The line a hand-off travels: Head ↔ agent bends around the graph; agent ↔ graph goes straight up. */
function path(from: string, to: string): string {
  const a = point(from), b = point(to);
  const headAgent = (from === 'head' && POS[to]) || (to === 'head' && POS[from]);
  if (headAgent) {   // around the graph, on the agent's side
    const agent = from === 'head' ? b : a;
    const cx = agent.x < GRAPH.x ? GRAPH.x - GRAPH.w / 2 - 120 : GRAPH.x + GRAPH.w / 2 + 120;
    return `M ${a.x} ${a.y} Q ${cx} ${GRAPH.y - 40} ${b.x} ${b.y}`;
  }
  return `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
}

interface Flight { id: number; d: string; color: string }

function ago(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
}

export function AgentView({ domain }: { domain: string }) {
  const [agents, setAgents] = useState<TeamAgent[]>([]);
  const [working, setWorking] = useState<Record<string, string[]>>({});
  const [log, setLog] = useState<ActivityEvent[]>([]);
  const [flights, setFlights] = useState<Flight[]>([]);
  const [recent, setRecent] = useState<Record<string, number>>({});   // agent → last hand-off time
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
        if (fresh.length) {
          setLog(l => [...fresh.slice().reverse(), ...l].slice(0, 8));
          const now = Date.now();
          setRecent(r => {
            const next = { ...r };
            for (const e of fresh) { next[e.source] = now; next[e.target] = now; }
            return next;
          });
          const added = fresh.slice(-6).map(e => ({
            id: ++flightId.current, d: path(e.source, e.target), color: KIND_COLOR[e.kind] ?? '#c8ced6',
          }));
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

  const title = Object.fromEntries(agents.map(a => [a.name, a.title]));
  const active = (name: string) => !!working[name] || (recent[name] ?? 0) > Date.now() - 6000;
  const headActive = active('head');
  const queued = Object.fromEntries(agents.map(a => [a.name, a.queued]));

  return (
    <div className="relative h-full w-full overflow-hidden">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="h-full w-full">
        <defs>
          <marker id="tip" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M2 1L8 5L2 9" fill="none" stroke="#8a93a0" strokeWidth={1.5} />
          </marker>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="6" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        {/* lines: Head ↔ graph, Head ↔ every agent (around the graph), every agent → graph, you ↔ Head */}
        <path d={path('head', 'graph')} stroke="#2a313d" strokeWidth={2} fill="none" />
        <path d={path('user', 'head')} stroke="#2a313d" strokeWidth={1.5} strokeDasharray="4 4" fill="none" />
        {Object.keys(POS).map(n => (
          <path key={n} d={path(n, 'graph')} stroke={active(n) ? '#3b4352' : '#232a35'} strokeWidth={1.2} fill="none" />
        ))}
        {/* the drawing's loops: the Head gives work down the left, the team reports back up the right */}
        <path d={`M ${HEAD.x - 42} ${HEAD.y + 6} C 60 ${HEAD.y + 20}, 20 ${GRAPH.y + 40}, ${POS.research.x - 20} ${POS.research.y - 28}`}
          stroke="#e2b35633" strokeWidth={1.5} strokeDasharray="5 6" fill="none" markerEnd="url(#tip)" />
        <path d={`M ${POS.reporter.x + 20} ${POS.reporter.y - 28} C ${W - 20} ${GRAPH.y + 40}, ${W - 60} ${HEAD.y + 20}, ${HEAD.x + 42} ${HEAD.y + 6}`}
          stroke="#4ade8033" strokeWidth={1.5} strokeDasharray="5 6" fill="none" markerEnd="url(#tip)" />
        <text x={70} y={GRAPH.y - 70} fill="#e2b35699" fontSize={11}>tasks · questions</text>
        <text x={W - 70} y={GRAPH.y - 70} textAnchor="end" fill="#4ade8099" fontSize={11}>reports · answers</text>
        {flights.map(f => <path key={`p${f.id}`} d={f.d} stroke={f.color} strokeOpacity={0.35} strokeWidth={1.2} fill="none" />)}

        {/* the knowledge graph */}
        <g>
          <rect x={GRAPH.x - GRAPH.w / 2} y={GRAPH.y - GRAPH.h / 2} width={GRAPH.w} height={GRAPH.h} rx={14}
            fill="#11151b" stroke={active('graph') ? '#a78bfa' : '#2a313d'} strokeWidth={active('graph') ? 2 : 1.2} />
          <text x={GRAPH.x} y={GRAPH.y - 6} textAnchor="middle" fill="#d5d9df" fontSize={17} fontWeight={500}>Knowledge graph</text>
          <text x={GRAPH.x} y={GRAPH.y + 16} textAnchor="middle" fill="#7c8592" fontSize={11.5}>facts only — checked against their sources</text>
        </g>

        {/* you and the Head */}
        <g>
          <circle cx={USER.x} cy={USER.y} r={20} fill="#11151b" stroke="#2a313d" />
          <text x={USER.x} y={USER.y + 4} textAnchor="middle" fill="#8a93a0" fontSize={11}>You</text>
        </g>
        <g filter={headActive ? 'url(#glow)' : undefined}>
          <circle cx={HEAD.x} cy={HEAD.y} r={40} fill="#1a1608" stroke="#e2b356" strokeWidth={headActive ? 3 : 1.5}>
            {headActive && <animate attributeName="r" values="40;44;40" dur="1.6s" repeatCount="indefinite" />}
          </circle>
          <text x={HEAD.x} y={HEAD.y + 6} textAnchor="middle" fill="#e2b356" fontSize={17} fontWeight={600}>Head</text>
        </g>
        {working.head && (
          <text x={HEAD.x + 52} y={HEAD.y + 5} fill="#e2b356" fontSize={12}>{working.head[0]}</text>
        )}

        {/* the team */}
        {TEAM_LABELS.map(l => (
          <text key={l.label} x={l.x} y={l.y} textAnchor="middle" fill="#4a5363" fontSize={11} letterSpacing={1.2}>
            {l.label.toUpperCase()}
          </text>
        ))}
        {Object.entries(POS).map(([name, p]) => {
          const on = active(name), doing = working[name]?.[0];
          return (
            <g key={name} filter={on ? 'url(#glow)' : undefined}>
              <rect x={p.x - 45} y={p.y - 17} width={90} height={34} rx={9}
                fill={on ? '#16202e' : '#11151b'} stroke={on ? '#5fb3f9' : '#2a313d'} strokeWidth={on ? 2 : 1.2} />
              <text x={p.x} y={p.y + 4} textAnchor="middle" fill={on ? '#e6eaf0' : '#a3abb7'} fontSize={12}>
                {title[name] ?? name}
              </text>
              {queued[name] > 0 && (
                <g>
                  <circle cx={p.x + 44} cy={p.y - 16} r={9} fill="#e2b356" />
                  <text x={p.x + 44} y={p.y - 12} textAnchor="middle" fill="#000" fontSize={10}>{queued[name]}</text>
                </g>
              )}
              {doing && (
                <text x={p.x} y={p.y + 31} textAnchor="middle" fill="#5fb3f9" fontSize={10.5}>
                  {doing.length > 22 ? doing.slice(0, 21) + '…' : doing}
                </text>
              )}
            </g>
          );
        })}

        {/* hand-offs in flight */}
        {flights.map(f => (
          <circle key={f.id} r={6} fill={f.color} filter="url(#glow)">
            <animateMotion dur={`${FLIGHT_MS}ms`} path={f.d} fill="freeze" />
          </circle>
        ))}
      </svg>

      <div className="pointer-events-none absolute top-3 left-3 w-[290px] space-y-1">
        {log.map(e => (
          <div key={e.seq} className="truncate rounded border border-line bg-panel/90 px-2 py-1 text-[10.5px] text-dim">
            <span style={{ color: KIND_COLOR[e.kind] ?? '#c8ced6' }}>●</span>{' '}
            <span className="text-ink">{title[e.source] ?? e.source}</span> → {title[e.target] ?? e.target}
            {' · '}{e.kind}{e.text ? `: ${e.text}` : ''} <span className="text-faint">{ago(e.at)}</span>
          </div>
        ))}
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 text-[10px] text-faint">
        {[['task', 'task'], ['report', 'report / answer'], ['tool', 'Head uses a tool'], ['write', 'facts stored']].map(([k, l]) => (
          <span key={k} className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: KIND_COLOR[k] }} />{l}</span>
        ))}
      </div>
    </div>
  );
}
