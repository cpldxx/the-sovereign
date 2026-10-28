import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Circle, CircleDot, Loader2, Play } from 'lucide-react';
import { kg, type ExamCore, type ExamItem, type ExamSitting, type ExamState } from '../lib/api';

const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
const when = (iso: string) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** The score over time, one exam: a 2px line on a fixed 0-100 axis, the latest value at its end; a crosshair finds any
 * sitting (arrow keys too) and the readout above the plot — never over it — tells it. */
function ScoreChart({ sittings }: { sittings: ExamSitting[] }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  const H = 132, L = 26, R = 30, T = 10, B = 20;
  const times = sittings.map(s => new Date(s.created_at).getTime());
  const t0 = Math.min(...times), t1 = Math.max(...times);
  const x = (i: number) => t1 === t0 ? L + (width - L - R) / 2 : L + ((times[i] - t0) / (t1 - t0)) * (width - L - R);
  const y = (score: number) => T + (1 - score / 100) * (H - T - B);
  const line = sittings.map((s, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(s.score)}`).join(' ');
  const last = sittings.length - 1;
  const shown = at ?? null;

  function nearest(clientX: number, rect: DOMRect) {
    const px = clientX - rect.left;
    let best = 0;
    sittings.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    return best;
  }

  return (
    <div ref={box}>
      <div className="h-[30px] text-[11px] leading-[15px]">
        {shown === null ? <span className="text-faint">Hover the line, or focus it and use ← → for any sitting</span> : (
          <>
            <div><span className="text-ink">{sittings[shown].score}</span><span className="text-faint"> / 100 — {when(sittings[shown].created_at)}</span></div>
            <div className="text-faint">{sittings[shown].answered} of {sittings[shown].total} answered · {sittings[shown].open} open ·{' '}
              {sittings[shown].graph.entities.toLocaleString()} entities, {sittings[shown].graph.facts.toLocaleString()} facts</div>
          </>
        )}
      </div>
      {width > 0 && (
        <svg width={width} height={H} role="img" tabIndex={0} className="outline-none focus-visible:ring-1 focus-visible:ring-line-2"
          aria-label={`Exam score over ${sittings.length} sittings, now ${sittings[last].score} of 100`}
          onPointerMove={e => setAt(nearest(e.clientX, e.currentTarget.getBoundingClientRect()))}
          onPointerLeave={() => setAt(null)}
          onFocus={() => setAt(last)} onBlur={() => setAt(null)}
          onKeyDown={e => {
            if (e.key === 'ArrowLeft') setAt(i => Math.max(0, (i ?? last) - 1));
            if (e.key === 'ArrowRight') setAt(i => Math.min(last, (i ?? last) + 1));
          }}>
          {[0, 50, 100].map(v => (
            <g key={v}>
              <line x1={L} x2={width - R} y1={y(v)} y2={y(v)} className="stroke-line" strokeWidth={1} />
              <text x={L - 6} y={y(v) + 3} textAnchor="end" className="fill-faint font-mono" fontSize={9.5} style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</text>
            </g>
          ))}
          <text x={L} y={H - 5} className="fill-faint" fontSize={10}>{day(sittings[0].created_at)}</text>
          {last > 0 && <text x={width - R} y={H - 5} textAnchor="end" className="fill-faint" fontSize={10}>{day(sittings[last].created_at)}</text>}
          {last > 0 && <path d={`${line} L ${x(last)} ${y(0)} L ${x(0)} ${y(0)} Z`} className="fill-accent" fillOpacity={0.06} />}
          {last > 0 && <path d={line} fill="none" className="stroke-accent" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
          {shown !== null && <line x1={x(shown)} x2={x(shown)} y1={T} y2={H - B} className="stroke-line-2" strokeWidth={1} />}
          {sittings.map((s, i) => (sittings.length <= 30 || i === last || i === shown) && (
            <circle key={s.uid} cx={x(i)} cy={y(s.score)} r={i === shown ? 5 : 4} className="fill-accent stroke-bg" strokeWidth={2} />
          ))}
          <text x={x(last) + 9} y={y(sittings[last].score) + 4} className="fill-ink font-mono" fontSize={11}>{sittings[last].score}</text>
        </svg>
      )}
    </div>
  );
}

const STATUS = {
  answered: { icon: Check, label: 'answered' },
  partial: { icon: CircleDot, label: 'partial' },
  open: { icon: Circle, label: 'open' },
} as const;

function Points({ points }: { points: ExamItem['points'] }) {
  return (
    <span className="flex shrink-0 gap-0.5 pt-1" title={['not answered', 'answered in part', 'answered'][points]}>
      {[0, 1].map(i => <span key={i} className={`h-1.5 w-1.5 rounded-full ${i < points ? 'bg-accent' : 'border border-line-2'}`} />)}
    </span>
  );
}

/** One core question: its score as a bar, its state in words, and — opened — each exam question with the graph's answer. */
function Core({ core, items }: { core: ExamCore; items: ExamItem[] }) {
  const [open, setOpen] = useState(false);
  const { icon: Icon, label } = STATUS[core.status];
  return (
    <li className="space-y-1.5">
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-start gap-2 text-left">
        {open ? <ChevronDown size={12} className="mt-0.5 shrink-0 text-faint" /> : <ChevronRight size={12} className="mt-0.5 shrink-0 text-faint" />}
        <span className="min-w-0 flex-1 space-y-1">
          <span className="block leading-snug text-ink">{core.question}</span>
          <span className="flex items-center gap-2">
            <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
              <span className="block h-1.5 rounded-full bg-accent" style={{ width: `${core.score}%` }} />
            </span>
            <span className="w-7 text-right font-mono text-[11px] text-ink">{core.score}</span>
            <span className="flex w-[72px] items-center gap-1 text-[10.5px] text-dim"><Icon size={11} />{label}</span>
          </span>
        </span>
      </button>
      {open && (
        <ul className="space-y-2 pl-5">
          {items.map(x => (
            <li key={x.id} className="flex gap-2">
              <Points points={x.points} />
              <div className="min-w-0 space-y-0.5">
                <p className="leading-snug text-dim">
                  {x.tier && <span className="mr-1.5 font-mono text-[9.5px] tracking-wider text-faint uppercase">{x.tier}</span>}{x.question}
                </p>
                <p className="text-[11.5px] leading-snug text-faint">{x.answer}
                  {x.points > 0 && x.sources > 0 && <span> · {x.sources} source{x.sources === 1 ? '' : 's'}{x.well_sourced ? ', confirmed' : ''}</span>}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * How the domain grows (kg/core/exam.py): the charter's exam, answered from the graph alone after every learning cycle —
 * the score over time, each core question's state, and what the graph still can't answer (where research goes next).
 */
export function GrowthPanel({ domain, editable, version }: { domain: string; editable: boolean; version: number }) {
  const [state, setState] = useState<ExamState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [table, setTable] = useState(false);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      setState(await kg.exam(domain));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain]);
  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => {
    if (!state?.running) return;
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [state?.running, load]);

  async function take(fresh = false) {
    setStarting(true);
    try {
      const r = await kg.takeExam(domain, fresh);
      if (r.skipped) setError(`Not taken: ${r.skipped}`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  if (!state) return <div className="p-4 text-xs text-faint">{error ?? 'Loading…'}</div>;
  const run = state.latest;
  const current = run ? state.history.filter(h => h.version === run.version) : [];
  const first = current[0];
  const delta = run && first && current.length > 1 ? run.score - first.score : null;
  const older = run ? state.history.filter(h => h.version !== run.version) : [];

  return (
    <div className="h-full space-y-5 overflow-y-auto p-4 text-[12px]">
      <p className="leading-snug text-dim">
        How much of your research charter this domain can answer. After every learning cycle it sits an exam drawn from
        the charter and answers each question from the knowledge graph alone — what the model knows by itself earns nothing.
      </p>
      {error && <p className="text-xs text-bad">{error}</p>}

      {!run ? (
        <section className="space-y-2 rounded-md border border-line p-3">
          <p className="text-ink">{state.running ? 'Taking the first exam…' : 'No exam yet.'}</p>
          <p className="text-dim">{state.exam || state.running
            ? 'The first sitting is the baseline: what the graph knows before research builds on it.'
            : 'The exam is drawn from the research charter (Settings › Charter). Once it exists, the exam is taken after each learning cycle.'}</p>
          {editable && !state.running && (
            <button onClick={() => void take()} disabled={starting} className="flex items-center gap-1.5 rounded-md border border-line-2 px-3 py-1.5 text-dim hover:text-ink disabled:opacity-50">
              {starting ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Take it now
            </button>
          )}
        </section>
      ) : (
        <>
          <section className="space-y-1">
            <div className="flex items-end gap-3">
              <span className="text-[52px] leading-none font-medium text-ink">{run.score}</span>
              <span className="pb-1.5 text-faint">/ 100</span>
              {delta !== null && (
                <span className="pb-1.5 text-dim">{delta >= 0 ? '+' : ''}{delta} since {day(first.created_at)}</span>
              )}
              <span className="ml-auto pb-1.5">
                {state.running ? (
                  <span className="flex items-center gap-1.5 text-[11px] text-faint"><Loader2 size={12} className="animate-spin" /> sitting now…</span>
                ) : editable && (
                  <button onClick={() => void take()} disabled={starting} className="flex items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1 text-[11px] text-dim hover:text-ink disabled:opacity-50">
                    {starting ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Take it now
                  </button>
                )}
              </span>
            </div>
            <p className="text-dim">
              {run.answered} of {run.total} questions answered · {run.partial} in part · {run.open} open
              <span className="text-faint"> — {when(run.created_at)}, from {run.graph.entities.toLocaleString()} entities and {run.graph.facts.toLocaleString()} facts</span>
            </p>
          </section>

          <section className="space-y-1.5">
            <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Score over time</div>
            <ScoreChart sittings={current} />
            {current.length === 1 && <p className="text-[10.5px] text-faint">One sitting so far — the line grows with every learning cycle.</p>}
            {older.length > 0 && (
              <p className="text-[10.5px] text-faint">The charter's questions changed on {day(current[0].created_at)}, so a new exam began; earlier scores are in the table.</p>
            )}
            <button onClick={() => setTable(t => !t)} className="text-[10.5px] text-faint underline hover:text-ink">{table ? 'Hide' : 'Show'} every sitting</button>
            {table && (
              <table className="w-full text-left text-[11px]" style={{ fontVariantNumeric: 'tabular-nums' }}>
                <thead className="text-faint">
                  <tr><th className="py-1 font-normal">Sitting</th><th className="font-normal">Score</th><th className="font-normal">Answered</th><th className="font-normal">Open</th><th className="font-normal">Facts</th></tr>
                </thead>
                <tbody className="text-dim">
                  {[...state.history].reverse().map(h => (
                    <tr key={h.uid} className="border-t border-line">
                      <td className="py-1">{when(h.created_at)}{h.version !== run.version && <span className="text-faint"> (exam {h.version})</span>}</td>
                      <td className="text-ink">{h.score}</td>
                      <td>{h.answered}/{h.total}</td>
                      <td>{h.open}</td>
                      <td>{h.graph.facts.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="space-y-2">
            <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Core questions</div>
            <ol className="space-y-3">
              {run.cores.map(c => <Core key={c.core} core={c} items={run.items.filter(x => x.core === c.core)} />)}
            </ol>
            <p className="text-[10.5px] text-faint">The open questions are what the next learning cycle researches first.</p>
            {editable && !state.running && (
              <button onClick={() => { if (confirm('Write new exam questions from the same charter and sit them now? The score starts a new series.')) void take(true); }}
                disabled={starting} className="text-[10.5px] text-faint underline hover:text-ink disabled:opacity-50">
                Questions off the mark? Set new ones
              </button>
            )}
          </section>
        </>
      )}
    </div>
  );
}
