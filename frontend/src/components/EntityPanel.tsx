import { useEffect, useState } from 'react';
import { ExternalLink, Loader2, X } from 'lucide-react';
import { kg, type EntityDetail } from '../lib/api';
import { categoryColor } from '../lib/colors';
import { FactRow } from './FactRow';

const isUrl = (s: string) => /^https?:\/\//.test(s);

export function EntityPanel({ domain, uid, entityTypes, version, onClose, onSelect }: {
  domain: string;
  uid: string;
  entityTypes: string[];
  version: number;              // bumps when the graph changes, to refetch
  onClose: () => void;
  onSelect: (uid: string) => void;
}) {
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    kg.entity(domain, uid)
      .then(d => { if (alive) setDetail(d); })
      .catch(e => { if (alive) setError((e as Error).message); });
    return () => { alive = false; };
  }, [domain, uid, version]);

  const e = detail?.entity.uid === uid ? detail.entity : null;
  const valid = detail?.facts.filter(f => f.valid) ?? [];
  const superseded = detail?.facts.filter(f => !f.valid) ?? [];

  return (
    <div className="absolute top-3 right-3 z-10 flex max-h-[calc(100%-24px)] w-[360px] flex-col overflow-hidden rounded-lg border border-line-2 bg-panel/95 shadow-2xl backdrop-blur max-sm:left-3 max-sm:w-auto">
      <div className="flex items-start justify-between gap-2 border-b border-line p-4">
        {e ? (
          <div className="min-w-0">
            <span
              className="rounded px-1.5 py-0.5 font-mono text-[10px]"
              style={{ background: categoryColor(e.type, entityTypes) + '26', color: categoryColor(e.type, entityTypes) }}
            >
              {e.type}
            </span>
            <h3 className="mt-1.5 text-[15px] font-medium">{e.name}</h3>
            {e.aliases && e.aliases.length > 0 && (
              <p className="text-[11px] text-faint">also: {e.aliases.join(', ')}</p>
            )}
            {e.summary && <p className="mt-1.5 text-[12px] leading-relaxed text-dim">{e.summary}</p>}
            <p className="mt-1.5 font-mono text-[10px] text-faint">
              {e.mentions ?? 1} mention{(e.mentions ?? 1) === 1 ? '' : 's'} · {valid.length} fact{valid.length === 1 ? '' : 's'}
            </p>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-faint">
            {error ?? <><Loader2 size={12} className="animate-spin" /> loading…</>}
          </div>
        )}
        <button onClick={onClose} className="shrink-0 text-faint hover:text-ink"><X size={14} /></button>
      </div>

      {e && (
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {valid.length === 0 && <p className="p-2.5 text-xs text-faint">No facts yet — only mentioned.</p>}
          {valid.map(f => <FactRow key={f.uid} fact={f} focusUid={uid} onSelect={onSelect} />)}

          {superseded.length > 0 && (
            <>
              <h4 className="px-2.5 pt-3 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">History</h4>
              {superseded.map(f => <FactRow key={f.uid} fact={f} focusUid={uid} onSelect={onSelect} />)}
            </>
          )}

          {detail && detail.episodes.length > 0 && (
            <>
              <h4 className="px-2.5 pt-3 pb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Sources</h4>
              <ul className="space-y-1 px-2.5 pb-2">
                {detail.episodes.map(ep => (
                  <li key={ep.uid} className="flex items-center gap-1.5 text-[11px]">
                    {isUrl(ep.source) ? (
                      <a href={ep.source} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-1 text-dim hover:text-ink">
                        <ExternalLink size={10} className="shrink-0" />
                        <span className="truncate">{ep.title || ep.source}</span>
                      </a>
                    ) : (
                      <span className="truncate text-dim">{ep.title || ep.source}</span>
                    )}
                    {ep.content_status === 'partial' && <span className="shrink-0 text-[9.5px] text-gold">partial</span>}
                    <span className="ml-auto shrink-0 font-mono text-[9.5px] text-faint">{ep.created_at?.slice(0, 10)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
