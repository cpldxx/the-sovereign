import { useCallback, useEffect, useState } from 'react';
import { Bot, Check, Loader2, X } from 'lucide-react';
import { kg, type Review } from '../lib/api';

const KIND_LABEL: Record<Review['kind'], string> = {
  fact: 'weak fact',
  merge: 'same entity?',
  link: 'restate / replace?',
};

const PAGE = 100;
const IDLE_POLLS = 2;          // the Head not reviewing on two polls in a row (10 s): the sweep is over
const SWEEP_MINUTES = 10;
const SWEEP_MINUTES_MAX = SWEEP_MINUTES + 5;   // stop following even if the activity feed never says so

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function Details({ review }: { review: Review }) {
  const p = review.payload as Record<string, string | number | string[] | { uid: string; fact: string }[]>;
  if (review.kind === 'fact') {
    return (
      <div className="text-[11.5px] text-dim">
        <span className="font-mono text-faint">{String(p.source_name)} —{String(p.relation)}→ {String(p.target_name)}</span>
        <div className="text-[10.5px] text-faint">from {String(p.source)}</div>
      </div>
    );
  }
  if (review.kind === 'merge') {
    return (
      <div className="text-[11.5px] text-dim">
        merge “{String(p.entity_name)}” into “{String(p.candidate_name)}” · similarity {String(p.similarity ?? '')}
      </div>
    );
  }
  const existing = (p.existing as { uid: string; fact: string }[]) ?? [];
  const mark = (uid: string) => (p.same_as === uid ? 'same as' : (p.supersedes as string[] | undefined)?.includes(uid) ? 'replaces' : '');
  return (
    <ul className="space-y-0.5 text-[11px] text-dim">
      {existing.map(e => (
        <li key={e.uid}>{mark(e.uid) && <span className="font-mono text-accent">{mark(e.uid)}: </span>}{e.fact}</li>
      ))}
    </ul>
  );
}

export function ReviewPanel({ domain, version, editable, onChanged }: {
  domain: string;
  version: number;              // bumps when the graph reloads (new items may have arrived)
  editable: boolean;            // editors decide; viewers only see the queue
  onChanged: () => Promise<void>;
}) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  const [busy, setBusy] = useState<string | null>(null);   // review uid being decided
  // The Head decides the queue as a KG sweep (2026-10-14: it was a chat turn, so the instruction and the Head's table
  // landed in your conversation and went back to it as history).
  const [sweeping, setSweeping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** The oldest `limit` pending items (the Head decides oldest first too), in pages the KG allows. */
  const load = useCallback(async (): Promise<number | null> => {
    try {
      const got: Review[] = [];
      let all = 0;
      for (let offset = 0; offset < limit; offset += 500) {
        const size = Math.min(500, limit - offset);
        const r = await kg.reviews(domain, 'pending', size, offset, 'oldest');
        got.push(...r.reviews);
        all = r.total;
        if (r.reviews.length < size) break;
      }
      setReviews(got);
      setTotal(all);
      return all;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [domain, limit]);

  useEffect(() => { void load(); }, [load, version]);

  // While the Head reviews: follow the queue until the sweep is over — the KG shows the Head "reviewing" while a
  // round runs. An unchanged count alone ended it early: one Head turn often takes minutes before its first decision.
  useEffect(() => {
    if (!sweeping) return;
    let idle = 0;
    const until = Date.now() + SWEEP_MINUTES_MAX * 60_000;
    const t = setInterval(async () => {
      const [n, a] = await Promise.all([load(), kg.activity(domain, Number.MAX_SAFE_INTEGER).catch(() => null)]);
      const over = Date.now() > until;
      if (!over && (n === null || a === null)) return;   // unreachable now: look again on the next tick
      const reviewing = !!a?.working.some(w => w.agent === 'head' && w.doing.some(x => x.startsWith('reviewing')));
      idle = reviewing ? 0 : idle + 1;
      if (over || n === 0 || idle >= IDLE_POLLS) { setSweeping(false); void onChanged(); }
    }, 5000);
    return () => clearInterval(t);
  }, [sweeping, domain, load, onChanged]);

  async function decide(uid: string, approve: boolean) {
    setBusy(uid); setError(null);
    try {
      await kg.decide(domain, uid, approve, 'decided in the UI');
      await load();
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function headReview() {
    setError(null);
    try {
      await kg.headReview(domain, SWEEP_MINUTES);   // started, or busy: a sweep runs already — either way, follow it
      setSweeping(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <p className="text-[11px] leading-snug text-faint">
          Ambiguous writes wait here: weak facts, possible duplicate entities, possible restatements.
          {total > 0 && <span className="text-dim"> {total} pending.</span>}
        </p>
        {editable && (
          <button
            onClick={headReview}
            disabled={!!busy || sweeping || !total}
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-accent/50 px-2.5 py-1.5 text-[11.5px] text-accent hover:bg-accent/10 disabled:opacity-40"
            title="Let the Head Agent decide the pending items, oldest first (up to 10 minutes)"
          >
            {sweeping ? <Loader2 size={12} className="animate-spin" /> : <Bot size={12} />} Head review
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && <p className="mb-2 text-xs text-bad">{error}</p>}
        {sweeping && (
          <p className="mb-3 flex items-center gap-1.5 rounded-md border border-accent/30 bg-accent/5 p-2.5 text-[11.5px] text-accent">
            <Loader2 size={12} className="animate-spin" /> The Head is reviewing — items leave the list as it decides them.
          </p>
        )}
        {reviews && reviews.length === 0 && <p className="mt-8 text-center text-xs text-faint">Nothing to review.</p>}
        <ul className="space-y-2.5">
          {reviews?.map(r => (
            <li key={r.uid} className="rounded-md border border-line bg-panel-2/50 p-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="rounded bg-accent/15 px-1.5 py-0.5 font-mono text-[10px] text-accent">{KIND_LABEL[r.kind]}</span>
                <span className="ml-auto font-mono text-[10px] text-faint">{r.created_at && when(r.created_at)}</span>
              </div>
              <p className="mb-1.5 text-[12.5px] leading-snug">{r.summary}</p>
              <Details review={r} />
              {editable && (
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() => decide(r.uid, true)}
                    disabled={!!busy}
                    className="flex items-center gap-1 rounded border border-good/40 px-2 py-1 text-[11px] text-good hover:bg-good/10 disabled:opacity-40"
                  >
                    {busy === r.uid ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Approve
                  </button>
                  <button
                    onClick={() => decide(r.uid, false)}
                    disabled={!!busy}
                    className="flex items-center gap-1 rounded border border-line-2 px-2 py-1 text-[11px] text-dim hover:text-bad disabled:opacity-40"
                  >
                    <X size={11} /> Dismiss
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
        {reviews && reviews.length < total && (
          <button onClick={() => setLimit(l => l + PAGE)}
            className="mt-3 w-full rounded-md border border-line-2 py-1.5 text-[11.5px] text-dim hover:text-ink">
            Showing the oldest {reviews.length} of {total} — load {Math.min(PAGE, total - reviews.length)} more
          </button>
        )}
      </div>
    </div>
  );
}
