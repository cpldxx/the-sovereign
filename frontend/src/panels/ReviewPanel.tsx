import { useCallback, useEffect, useState } from 'react';
import { Bot, Check, Loader2, X } from 'lucide-react';
import { hermes, kg, type Review } from '../lib/api';
import { Markdown } from '../components/Markdown';

const KIND_LABEL: Record<Review['kind'], string> = {
  fact: 'weak fact',
  merge: 'same entity?',
  link: 'restate / replace?',
};

const HEAD_INSTRUCTION =
  'Process the pending review queue: inspect each item (use get_entity or query_knowledge_graph when it helps), ' +
  'decide it with resolve_review and a short note, then summarize your decisions in a short table.';

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
        <li key={e.uid}>{mark(e.uid) && <span className="font-mono text-gold">{mark(e.uid)}: </span>}{e.fact}</li>
      ))}
    </ul>
  );
}

export function ReviewPanel({ domain, version, onChanged }: {
  domain: string;
  version: number;              // bumps when the graph reloads (new items may have arrived)
  onChanged: () => Promise<void>;
}) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);   // review uid being decided, or 'head'
  const [headText, setHeadText] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setReviews(await kg.reviews(domain)); } catch (e) { setError((e as Error).message); }
  }, [domain]);

  useEffect(() => { void load(); }, [load, version]);

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
    setBusy('head'); setError(null); setHeadText('');
    try {
      await hermes.askStream(domain, HEAD_INSTRUCTION, [], e => {
        if (e.type === 'answer') setHeadText(e.text);
        if (e.type === 'error') setError(e.detail);
        if (e.type === 'tool_end' && e.name === 'resolve_review') void load();
      }, undefined, 25);
      await load();
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <p className="text-[11px] leading-snug text-faint">
          Ambiguous writes wait here: weak facts, possible duplicate entities, possible restatements.
        </p>
        <button
          onClick={headReview}
          disabled={!!busy || !reviews?.length}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-gold/50 px-2.5 py-1.5 text-[11.5px] text-gold hover:bg-gold/10 disabled:opacity-40"
          title="Let the Head Agent decide every pending item"
        >
          {busy === 'head' ? <Loader2 size={12} className="animate-spin" /> : <Bot size={12} />} Head review
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && <p className="mb-2 text-xs text-bad">{error}</p>}
        {headText && (
          <div className="mb-3 rounded-md border border-gold/30 bg-gold/5 p-3"><Markdown>{headText}</Markdown></div>
        )}
        {reviews && reviews.length === 0 && <p className="mt-8 text-center text-xs text-faint">Nothing to review.</p>}
        <ul className="space-y-2.5">
          {reviews?.map(r => (
            <li key={r.uid} className="rounded-md border border-line bg-panel-2/50 p-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="rounded bg-gold/15 px-1.5 py-0.5 font-mono text-[10px] text-gold">{KIND_LABEL[r.kind]}</span>
                <span className="ml-auto font-mono text-[10px] text-faint">{r.created_at?.slice(0, 16).replace('T', ' ')}</span>
              </div>
              <p className="mb-1.5 text-[12.5px] leading-snug">{r.summary}</p>
              <Details review={r} />
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
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
