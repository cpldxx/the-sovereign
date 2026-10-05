import type { Fact } from '../lib/api';

/** One synapse: relation, the other entity, the fact text, its strength and evidence. */
export function FactRow({ fact, focusUid, onSelect }: {
  fact: Fact;
  focusUid?: string;            // the entity we're looking from (shows the *other* end)
  onSelect?: (uid: string) => void;
}) {
  const state = fact.source_uid === fact.target_uid;
  const outgoing = fact.source_uid === focusUid;
  const otherUid = outgoing ? fact.target_uid : fact.source_uid;
  const otherName = outgoing ? fact.target_name : fact.source_name;

  return (
    <div className={`rounded-md px-2.5 py-2 ${fact.valid ? '' : 'opacity-55'}`}>
      <div className="mb-0.5 flex items-center gap-1.5 text-[10.5px]">
        {state ? (
          <span className="font-mono text-faint">state</span>
        ) : focusUid ? (
          <>
            <span className="font-mono text-gold">{outgoing ? `${fact.relation} →` : `← ${fact.relation}`}</span>
            <button onClick={() => onSelect?.(otherUid)} className="truncate text-dim hover:text-ink">{otherName}</button>
          </>
        ) : (
          <>
            <button onClick={() => onSelect?.(fact.source_uid)} className="truncate text-dim hover:text-ink">{fact.source_name}</button>
            <span className="shrink-0 font-mono text-gold">{fact.relation} →</span>
            <button onClick={() => onSelect?.(fact.target_uid)} className="truncate text-dim hover:text-ink">{fact.target_name}</button>
          </>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5" title={`weight ${fact.weight} · ${fact.evidence} source(s)`}>
          <span className="h-1 w-10 rounded bg-line">
            <span className="block h-1 rounded bg-gold" style={{ width: `${fact.weight * 100}%` }} />
          </span>
          <span className="font-mono text-faint">{fact.weight.toFixed(2)} · {fact.evidence}×</span>
        </span>
      </div>
      <p className={`text-[12.5px] leading-snug ${fact.valid ? 'text-ink' : 'text-dim line-through'}`}>{fact.fact}</p>
      {!fact.valid && fact.invalid_reason && (
        <p className="mt-0.5 text-[10.5px] text-faint">superseded: {fact.invalid_reason.replace(/^superseded by f_\w+: /, '')}</p>
      )}
    </div>
  );
}
