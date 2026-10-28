import { useCallback, useEffect, useState } from 'react';
import { Check, Compass, Loader2, Play, Plus, RotateCcw, X } from 'lucide-react';
import { kg, type Charter, type CharterDepth, type CharterState } from '../lib/api';

const DEPTHS: { id: CharterDepth; label: string; hint: string }[] = [
  { id: 'overview', label: 'Overview', hint: 'the big picture: what it is, who matters, where it is going' },
  { id: 'working', label: 'Working', hint: 'enough to discuss it and act on it' },
  { id: 'expert', label: 'Expert', hint: 'primary literature, methods, evidence quality, open debates' },
];
const KIND_LABEL = { question: 'Core question', scope: 'In scope', exclude: 'Leave out' } as const;

function when(iso?: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

/** A list of short texts: each editable in place, removable, and one more to add. */
function Items({ items, editable, placeholder, numbered, onChange }: {
  items: string[];
  editable: boolean;
  placeholder: string;
  numbered?: boolean;
  onChange: (items: string[]) => void;
}) {
  const [adding, setAdding] = useState('');
  const input = 'min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-2 py-1 text-[12px] outline-none focus:border-accent/60';
  function add() {
    if (!adding.trim()) return;
    onChange([...items, adding.trim()]);
    setAdding('');
  }
  return (
    <div className="space-y-1">
      {items.map((item, i) => (
        <div key={`${i}-${item}`} className="flex items-start gap-1.5">
          {numbered && <span className="w-4 shrink-0 pt-1 text-right font-mono text-[10.5px] text-faint">{i + 1}</span>}
          {editable ? (
            <>
              <input defaultValue={item} className={input}
                onBlur={e => { const v = e.target.value.trim(); if (v && v !== item) onChange(items.map((x, j) => j === i ? v : x)); }} />
              <button onClick={() => onChange(items.filter((_, j) => j !== i))} className="pt-1 text-faint hover:text-bad" title="Remove"><X size={12} /></button>
            </>
          ) : <span className="text-ink">{item}</span>}
        </div>
      ))}
      {editable && (
        <div className="flex items-center gap-1.5">
          {numbered && <span className="w-4 shrink-0" />}
          <input value={adding} onChange={e => setAdding(e.target.value)} placeholder={placeholder} className={input}
            onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); add(); } }} />
          <button onClick={add} disabled={!adding.trim()} className="rounded border border-line-2 p-1 text-dim hover:text-ink disabled:opacity-30" title="Add">
            <Plus size={12} />
          </button>
        </div>
      )}
      {!editable && !items.length && <p className="text-faint">—</p>}
    </div>
  );
}

/**
 * What you want from this domain (kg/core/charter.py). The Head drafts it from a short interview; once you confirm it
 * the first deep research starts, and from then on every research run steers by it.
 */
export function CharterPanel({ domain, editable, onAskHead }: {
  domain: string;
  editable: boolean;
  onAskHead: () => void;
}) {
  const [state, setState] = useState<CharterState | null>(null);
  const [busy, setBusy] = useState<'draft' | 'start' | 'reset' | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await kg.charter(domain));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain]);
  useEffect(() => { void load(); }, [load]);

  async function run(what: typeof busy, call: () => Promise<CharterState>, done?: (s: CharterState) => string | null) {
    setBusy(what);
    setError(null);
    try {
      const s = await call();
      setState(s);
      setNote(done?.(s) ?? null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const save = (changes: Partial<Charter>) => run(null, () => kg.saveCharter(domain, changes), () => 'Saved');

  if (!state) return <div className="p-4 text-xs text-faint">{error ?? 'Loading…'}</div>;
  const c = state.charter;

  return (
    <div className="h-full space-y-5 overflow-y-auto p-4 text-[12px]">
      <p className="flex gap-2 leading-snug text-dim">
        <Compass size={14} className="mt-0.5 shrink-0 text-accent" />
        <span>What you want from this domain. Every research run steers by it: the first deep research, the questions each
          learning cycle pursues, and what the Head pays attention to.</span>
      </p>
      {error && <p className="text-xs text-bad">{error}</p>}

      {!c ? (
        <section className="space-y-3 rounded-md border border-line p-3">
          <p className="text-ink">No charter yet.</p>
          <p className="text-dim">Tell the Head what you want to find out. It asks a few questions, drafts the charter, and starts
            researching once you agree.</p>
          <div className="flex flex-wrap gap-2">
            <button onClick={onAskHead} className="rounded-md bg-accent px-3 py-1.5 text-black">Talk to the Head</button>
            {editable && (
              <button onClick={() => void run('draft', () => kg.draftCharter(domain), () => 'Drafted from the description — check it below')}
                disabled={busy !== null} className="flex items-center gap-1.5 rounded-md border border-line-2 px-3 py-1.5 text-dim hover:text-ink disabled:opacity-50">
                {busy === 'draft' && <Loader2 size={12} className="animate-spin" />} Draft it from the description
              </button>
            )}
          </div>
          {busy === 'draft' && <p className="text-[10.5px] text-faint">Writing — about a minute with the local model…</p>}
        </section>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-widest uppercase ${state.status === 'active' ? 'border-accent/50 text-accent' : 'border-line-2 text-dim'}`}>
              {state.status === 'active' ? 'Active' : 'Draft'}
            </span>
            <span className="text-[10.5px] text-faint">
              {state.status === 'active' ? `steering research since ${when(state.confirmed_at)}` : 'no research has started from it yet'}
            </span>
            {note && <span className="ml-auto text-[10.5px] text-faint">{note}</span>}
          </div>

          {state.status === 'draft' && editable && (
            <section className="space-y-2 rounded-md border border-accent/40 p-3">
              <p className="text-ink">Does this capture what you want?</p>
              <p className="text-dim">Starting runs the first deep research: a broad first study of the topic, then one research run
                per core question. It works in the background (hours with a local model); the graph fills as it reads.</p>
              <button onClick={() => void run('start', () => kg.confirmCharter(domain), s =>
                  s.kickoff?.error ? `Confirmed — ${s.kickoff.error}` : `Research started: ${s.kickoff?.jobs?.length ?? 0} runs queued`)}
                disabled={busy !== null} className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-black disabled:opacity-50">
                {busy === 'start' ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Start researching
              </button>
            </section>
          )}

          <section className="space-y-1.5">
            <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Goal</div>
            {editable ? (
              <textarea key={c.goal} defaultValue={c.goal} rows={3}
                onBlur={e => { const v = e.target.value.trim(); if (v && v !== c.goal) void save({ goal: v }); }}
                className="w-full resize-none rounded-md border border-line-2 bg-bg px-2 py-1.5 text-[12.5px] leading-snug outline-none focus:border-accent/60" />
            ) : <p className="text-[12.5px] leading-snug text-ink">{c.goal}</p>}
          </section>

          <section className="space-y-1.5">
            <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Core questions</div>
            <p className="text-[10.5px] text-faint">What this domain's knowledge must be able to answer — most important first.</p>
            <Items items={c.questions} editable={editable} numbered placeholder="Add a question…"
              onChange={questions => questions.length ? void save({ questions }) : setError('Keep at least one core question')} />
          </section>

          <section className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <div className="font-mono text-[10px] tracking-widest text-faint uppercase">In scope</div>
              <Items items={c.scope} editable={editable} placeholder="A sub-topic, period, region…" onChange={scope => void save({ scope })} />
            </div>
            <div className="space-y-1.5">
              <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Leave out</div>
              <Items items={c.exclude} editable={editable} placeholder="What not to research…" onChange={exclude => void save({ exclude })} />
            </div>
          </section>

          <section className="space-y-1.5">
            <div className="font-mono text-[10px] tracking-widest text-faint uppercase">Depth</div>
            <div className="grid gap-1.5 sm:grid-cols-3">
              {DEPTHS.map(d => (
                <button key={d.id} disabled={!editable} onClick={() => d.id !== c.depth && void save({ depth: d.id })}
                  className={`rounded-md border px-2.5 py-2 text-left ${c.depth === d.id ? 'border-accent/60 bg-panel-2' : 'border-line-2 hover:border-line-2/80'} disabled:cursor-default`}>
                  <span className={`block ${c.depth === d.id ? 'text-ink' : 'text-dim'}`}>{d.label}</span>
                  <span className="block text-[10.5px] leading-snug text-faint">{d.hint}</span>
                </button>
              ))}
            </div>
          </section>

          {state.suggestions.length > 0 && (
            <section className="space-y-1.5">
              <div className="font-mono text-[10px] tracking-widest text-faint uppercase">The Head suggests</div>
              {state.suggestions.map(s => (
                <div key={s.uid} className="flex items-start gap-2 rounded-md border border-line p-2.5">
                  <div className="min-w-0 flex-1">
                    <span className="text-[10.5px] text-faint">{KIND_LABEL[s.kind]}</span>
                    <p className="text-ink">{s.text}</p>
                    {s.why && <p className="text-[10.5px] text-faint">{s.why}</p>}
                  </div>
                  {editable && (
                    <div className="flex shrink-0 gap-1">
                      <button onClick={() => void run(null, () => kg.decideCharterSuggestion(domain, s.uid, true))}
                        className="rounded border border-line-2 p-1 text-dim hover:text-ink" title="Add it"><Check size={12} /></button>
                      <button onClick={() => void run(null, () => kg.decideCharterSuggestion(domain, s.uid, false))}
                        className="rounded border border-line-2 p-1 text-dim hover:text-bad" title="Dismiss"><X size={12} /></button>
                    </div>
                  )}
                </div>
              ))}
            </section>
          )}

          {editable && (
            <button onClick={() => {
                if (confirm('Start over? The Head will interview you again. Nothing the graph has learned is lost.')) {
                  void run('reset', () => kg.resetCharter(domain));
                }
              }}
              disabled={busy !== null} className="flex items-center gap-1.5 text-[11px] text-faint hover:text-ink disabled:opacity-50">
              <RotateCcw size={11} /> Start over with a new interview
            </button>
          )}
        </>
      )}
    </div>
  );
}
