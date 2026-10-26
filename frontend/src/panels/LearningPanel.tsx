import { useCallback, useEffect, useState } from 'react';
import { Brain, Loader2, Play, Plus, X } from 'lucide-react';
import { kg, research, type LearningCycle, type LearningGaps, type LearningSettings } from '../lib/api';

const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

function when(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * How this graph grows by itself (kg/core/learning.py): at the times you set it reads what's new, researches what it
 * is unsure or silent about, keeps what its sources confirm and forgets what none ever did — then briefs you.
 */
export function LearningPanel({ domain, editable }: { domain: string; editable: boolean }) {
  const [s, setS] = useState<LearningSettings | null>(null);
  const [last, setLast] = useState<LearningCycle | null>(null);
  const [gaps, setGaps] = useState<LearningGaps | null>(null);
  const [adding, setAdding] = useState('09:00');
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await kg.learning(domain);
      setS(r.settings);
      setLast(r.last);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain]);
  useEffect(() => { void load(); kg.learningGaps(domain).then(setGaps).catch(() => undefined); }, [domain, load]);

  async function save(changes: Partial<LearningSettings>) {
    try {
      setS(await kg.saveLearning(domain, changes));
      setNote('Saved');
      setTimeout(() => setNote(null), 1500);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function runNow() {
    setStarting(true);
    try {
      await research.cycle(domain);
      setNote('A cycle started — its research runs show under the Researcher on the left');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  if (!s) return <div className="p-4 text-xs text-faint">{error ?? 'Loading…'}</div>;
  const input = 'rounded-md border border-line-2 bg-bg px-2 py-1 text-[12px] outline-none focus:border-accent/60';
  const learned = (last?.runs ?? []).reduce((n, r) => n + (r.summary?.facts_created ?? 0), 0);
  const confirmed = (last?.runs ?? []).reduce((n, r) => n + (r.summary?.facts_strengthened ?? 0), 0);

  return (
    <div className="h-full space-y-5 overflow-y-auto p-4 text-[12px]">
      <p className="flex gap-2 leading-snug text-dim">
        <Brain size={14} className="mt-0.5 shrink-0 text-accent" />
        <span>This graph learns by itself. At the times below it reads what's new, researches what it is unsure or
          silent about, keeps what its sources confirm, forgets what none ever did, and writes your briefing.</span>
      </p>
      {error && <p className="text-xs text-bad">{error}</p>}

      <section className="space-y-2">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={s.enabled} disabled={!editable} onChange={e => void save({ enabled: e.target.checked })} />
          <span className="text-ink">Learn by itself</span>
          {note && <span className="ml-auto text-[10.5px] text-faint">{note}</span>}
        </label>
        <div className={s.enabled ? '' : 'opacity-40'}>
          <div className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">When</div>
          <div className="flex flex-wrap items-center gap-1.5">
            {s.times.map(t => (
              <span key={t} className="flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 font-mono text-ink">
                {t}
                {editable && s.times.length > 1 && (
                  <button onClick={() => void save({ times: s.times.filter(x => x !== t) })} className="text-faint hover:text-bad" title="Remove"><X size={11} /></button>
                )}
              </span>
            ))}
            {editable && (
              <span className="flex items-center gap-1">
                <input type="time" value={adding} onChange={e => setAdding(e.target.value)} className={`${input} w-[92px] font-mono`} />
                <button onClick={() => void save({ times: [...s.times, adding] })} className="rounded border border-line-2 p-1 text-dim hover:text-ink" title="Add a time">
                  <Plus size={12} />
                </button>
              </span>
            )}
          </div>
          <p className="mt-1 text-[10.5px] text-faint">
            {s.timezone}
            {editable && s.timezone !== browserZone && (
              <> · <button onClick={() => void save({ timezone: browserZone })} className="underline hover:text-ink">use {browserZone}</button></>
            )}
            {' '}· each time runs once a day; the Mac must be on
          </p>
        </div>
      </section>

      <section className={`grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-2 ${s.enabled ? '' : 'opacity-40'}`}>
        <span className="text-dim">Questions it researches on its own each time
          <span className="block text-[10.5px] text-faint">picked from what the graph is unsure or silent about</span></span>
        <select value={s.curiosity} disabled={!editable} onChange={e => void save({ curiosity: Number(e.target.value) })} className={input}>
          {[0, 1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <span className="text-dim">Forget a fact from one weak source nobody confirmed after
          <span className="block text-[10.5px] text-faint">it stays as history, never as an answer · 0 = never</span></span>
        <span className="flex items-center gap-1">
          <input type="number" min={0} max={365} defaultValue={s.forget_after_days} disabled={!editable} key={s.forget_after_days}
            onBlur={e => Number(e.target.value) !== s.forget_after_days && void save({ forget_after_days: Number(e.target.value) })}
            className={`${input} w-[60px] text-right font-mono`} />
          <span className="text-faint">days</span>
        </span>
      </section>

      {editable && (
        <button onClick={() => void runNow()} disabled={starting}
          className="flex items-center gap-1.5 rounded-md border border-line-2 px-3 py-1.5 text-dim hover:text-ink disabled:opacity-50">
          {starting ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Run a cycle now
        </button>
      )}

      <section className="space-y-1.5">
        <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Last cycle</div>
        {!last ? <p className="text-faint">None yet under the new schedule.</p> : (
          <div className="space-y-1.5 rounded-md border border-line p-3">
            <p className="text-dim">{when(last.started_at)}{last.finished_at ? ` → ${when(last.finished_at)}` : ' — running'}
              {' · '}learned <b className="text-ink">{learned}</b> facts, confirmed <b className="text-ink">{confirmed}</b>,
              forgot <b className="text-ink">{last.forgotten?.facts ?? 0}</b>{last.forgotten?.entities ? ` (and ${last.forgotten.entities} entities)` : ''}</p>
            {last.questions.length > 0 && (
              <ul className="space-y-1">
                {last.questions.map(q => (
                  <li key={q.question} className="leading-snug">
                    <span className="text-ink">{q.question}</span>
                    <span className="block text-[10.5px] text-faint">{q.why}</span>
                  </li>
                ))}
              </ul>
            )}
            {last.runs.filter(r => r.status === 'failed').map(r => (
              <p key={r.id} className="text-[10.5px] text-faint">{r.mode} failed — {r.error}</p>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-1.5">
        <div className="font-mono text-[10px] tracking-widest text-faint uppercase">What it is unsure about now</div>
        {!gaps ? <p className="text-faint">…</p> : (
          <ul className="space-y-1 text-dim">
            {gaps.weak.length > 0 && <li>One weak source only: {gaps.weak.slice(0, 5).map(w => w.entity).join(', ')}</li>}
            {gaps.stale.length > 0 && <li>Nothing new for a month: {gaps.stale.slice(0, 5).map(w => w.entity).join(', ')}</li>}
            {gaps.unanswered.length > 0 && <li>You asked, but it's not in the graph: {gaps.unanswered.slice(-3).map(q => `“${q.slice(0, 60)}”`).join(', ')}{gaps.unanswered_total > gaps.unanswered.length ? ` (${gaps.unanswered_total} such questions from everyone)` : ''}</li>}
            {gaps.ontology.length > 0 && <li>No words for yet: {gaps.ontology.slice(0, 5).join(', ')}</li>}
            {!gaps.weak.length && !gaps.stale.length && !gaps.unanswered.length && !gaps.ontology.length && <li className="text-faint">Nothing stands out.</li>}
          </ul>
        )}
      </section>
    </div>
  );
}
