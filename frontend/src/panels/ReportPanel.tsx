import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, ExternalLink, Loader2, Newspaper, RefreshCw, Square, Volume2 } from 'lucide-react';
import { kg, LANGFUSE_URL, type Report, type ReportFact } from '../lib/api';
import { Markdown } from '../components/Markdown';
import { canSpeak, speak, stopSpeaking } from '../lib/voice';

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function minutes(seconds: number) {
  return seconds < 90 ? `${Math.round(seconds)}s` : `${Math.round(seconds / 60)}m`;
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'good' | 'bad' | 'gold' }) {
  const color = !value ? 'text-faint' : tone === 'bad' ? 'text-bad' : tone === 'gold' ? 'text-gold' : tone === 'good' ? 'text-good' : 'text-ink';
  return (
    <div className="rounded-md border border-line bg-panel-2/40 px-2 py-1.5">
      <div className={`font-mono text-[14px] ${color}`}>{value}</div>
      <div className="text-[10px] text-faint">{label}</div>
    </div>
  );
}

function FactList({ title, facts, onSelect, struck }: {
  title: string;
  facts: ReportFact[];
  onSelect: (uid: string) => void;
  struck?: boolean;
}) {
  if (!facts.length) return null;
  return (
    <section>
      <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">{title} ({facts.length})</h4>
      <ul className="space-y-1">
        {facts.map(f => (
          <li key={f.uid} className="text-[11.5px] leading-snug">
            <span className={struck ? 'text-faint line-through' : 'text-dim'}>{f.fact}</span>{' '}
            <span className="font-mono text-[10px] text-faint">
              {f.weight.toFixed(2)}{f.evidence > 1 ? ` · ${f.evidence} sources` : ''} ·{' '}
              <button onClick={() => onSelect(f.source_uid)} className="hover:text-ink">{f.source_name}</button>
              {f.target_uid !== f.source_uid && <> → <button onClick={() => onSelect(f.target_uid)} className="hover:text-ink">{f.target_name}</button></>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ReportView({ domain, summary, onHighlight, onSelect }: {
  domain: string;
  summary: Report;
  onHighlight: (uids: Set<string>) => void;
  onSelect: (uid: string) => void;
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [details, setDetails] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  useEffect(() => {
    let alive = true;
    kg.report(domain, summary.uid).then(r => { if (alive) setReport(r); }).catch(() => {});
    return () => { alive = false; stopSpeaking(); };
  }, [domain, summary.uid]);

  const r = report ?? summary;
  const s = r.stats;
  const digest = report?.digest;

  function toggleSpeak() {
    if (speaking) { stopSpeaking(); setSpeaking(false); return; }
    speak(r.spoken || r.headline);
    setSpeaking(true);
    const t = setInterval(() => { if (!speechSynthesis.speaking) { setSpeaking(false); clearInterval(t); } }, 500);
  }

  function highlightAll() {
    if (!digest) return;
    const uids = new Set<string>(digest.entities.map(e => e.uid));
    for (const f of [...digest.facts_created, ...digest.facts_strengthened, ...digest.facts_invalidated]) {
      uids.add(f.source_uid);
      uids.add(f.target_uid);
    }
    onHighlight(uids);
  }

  return (
    <article className="space-y-3">
      <header>
        <div className="flex items-start gap-2">
          <h3 className="flex-1 text-[14px] leading-snug text-ink">{r.headline}</h3>
          {canSpeak && (
            <button onClick={toggleSpeak} className={`shrink-0 rounded p-1 ${speaking ? 'text-gold' : 'text-faint hover:text-ink'}`} title={speaking ? 'Stop' : 'Read aloud'}>
              {speaking ? <Square size={13} /> : <Volume2 size={14} />}
            </button>
          )}
        </div>
        <p className="mt-0.5 font-mono text-[10px] text-faint">
          {when(r.period_start)} → {when(r.period_end)} · written in {minutes(s.seconds)}
        </p>
      </header>

      <div className="grid grid-cols-4 gap-1.5">
        <Stat label="sources" value={s.changes.sources} />
        <Stat label="new entities" value={s.changes.entities_created} />
        <Stat label="new facts" value={s.changes.facts_created} tone="good" />
        <Stat label="strengthened" value={s.changes.facts_strengthened} tone="good" />
        <Stat label="superseded" value={s.changes.facts_invalidated} tone="gold" />
        <Stat label="to review" value={s.graph.pending_reviews} tone="gold" />
        <Stat label="research runs" value={s.research.runs} />
        <Stat label="runs failed" value={s.research.failed} tone="bad" />
      </div>

      <Markdown>{r.briefing}</Markdown>

      {s.ontology_gaps?.length > 0 && (
        <p className="text-[11px] text-dim" title="Items dropped because the ontology has no such type or relation — evidence for changing it (Ontology tab)">
          <span className="font-mono text-[10px] tracking-widest text-faint uppercase">Ontology gaps </span>
          {s.ontology_gaps.map(g => `${g.kind} “${g.name}” ×${g.count}`).join(' · ')}
        </p>
      )}

      <p className="font-mono text-[10px] text-faint">
        {!s.research.reachable
          ? 'research service was unreachable'
          : `${s.research.pages_read} pages read`
            + (s.research.seconds.research + s.research.seconds.ingest > 0  // runs before Phase D have no timings
              ? ` · research ${minutes(s.research.seconds.research)} · ingest ${minutes(s.research.seconds.ingest)}`
              : '')}
        {' · '}{s.summaries.refreshed} summaries refreshed
        {s.llm && s.llm.length > 0 && (
          <>
            <br />
            LLM, all domains: {s.llm.map(u => `${u.activity} ${u.calls} calls · ${(u.tokens / 1000).toFixed(0)}k tokens`).join(' | ')}
          </>
        )}
      </p>

      {digest && (
        <div className="border-t border-line pt-2">
          <div className="flex items-center gap-2">
            <button onClick={() => setDetails(o => !o)} className="flex items-center gap-1 font-mono text-[10px] tracking-widest text-faint uppercase hover:text-dim">
              {details ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Details
            </button>
            <button onClick={highlightAll} className="ml-auto rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-ink">
              Highlight in graph
            </button>
          </div>
          {details && (
            <div className="mt-2 space-y-3">
              <FactList title="New facts" facts={digest.facts_created} onSelect={onSelect} />
              <FactList title="Strengthened" facts={digest.facts_strengthened} onSelect={onSelect} />
              <FactList title="Superseded" facts={digest.facts_invalidated} onSelect={onSelect} struck />
              {digest.sources.length > 0 && (
                <section>
                  <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Sources ({digest.sources.length})</h4>
                  <ul className="space-y-0.5">
                    {digest.sources.map(e => (
                      <li key={e.uid} className="text-[11.5px]">
                        <a href={/^https?:/.test(e.source) ? e.source : undefined} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-dim hover:text-ink">
                          <ExternalLink size={10} className="shrink-0" />
                          <span className="truncate">{e.title || e.source}</span>
                          {e.content_status === 'partial' && <span className="shrink-0 text-[9.5px] text-gold">partial</span>}
                        </a>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export function ReportPanel({ domain, version, onHighlight, onSelect }: {
  domain: string;
  version: number;
  onHighlight: (uids: Set<string>) => void;
  onSelect: (uid: string) => void;
}) {
  const [reports, setReports] = useState<Report[] | null>(null);
  const [generating, setGenerating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [tracing, setTracing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await kg.reports(domain);
      setReports(r.reports);
      setGenerating(r.generating);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setReports(rs => rs ?? []);
    }
  }, [domain]);

  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => { kg.health().then(h => setTracing(h.tracing)).catch(() => {}); }, []);
  useEffect(() => {
    if (!generating) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [generating, load]);

  async function generate() {
    setError(null);
    try {
      await kg.createReport(domain, 24);
      setGenerating(true);
      setOpen(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const current = reports?.find(r => r.uid === open) ?? reports?.[0];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line p-3">
        <p className="flex-1 text-[11px] leading-snug text-faint">
          Written every night after research: what the graph learned, what changed, what needs you.
        </p>
        {tracing && (
          <a href={LANGFUSE_URL} target="_blank" rel="noreferrer" className="shrink-0 text-[10.5px] text-faint hover:text-ink" title="Agent traces (LangFuse)">
            traces ↗
          </a>
        )}
        <button
          onClick={generate}
          disabled={generating}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1.5 text-[11.5px] text-dim hover:text-ink disabled:opacity-50"
          title="Write a report for the last 24 hours now"
        >
          {generating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {generating ? 'Writing…' : 'Report now'}
        </button>
      </div>
      {error && <p className="px-3 pt-2 text-xs text-bad">{error}</p>}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {reports && reports.length === 0 && !generating && (
          <div className="mt-8 flex flex-col items-center gap-2 text-center">
            <Newspaper size={26} className="text-faint" />
            <p className="text-xs text-faint">No reports yet. One is written every night — or write one now.</p>
          </div>
        )}
        {generating && (
          <p className="flex items-center gap-1.5 text-[11.5px] text-gold">
            <Loader2 size={12} className="animate-spin" /> Refreshing summaries and writing the report — a few minutes.
          </p>
        )}
        {current && (
          <ReportView key={current.uid} domain={domain} summary={current} onHighlight={onHighlight} onSelect={onSelect} />
        )}
        {reports && reports.length > 1 && (
          <section className="border-t border-line pt-2">
            <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Earlier</h4>
            <ul>
              {reports.filter(r => r.uid !== current?.uid).map(r => (
                <li key={r.uid}>
                  <button onClick={() => setOpen(r.uid)} className="flex w-full gap-2 py-1 text-left text-[11.5px] text-dim hover:text-ink">
                    <span className="shrink-0 font-mono text-[10px] text-faint">{when(r.created_at)}</span>
                    <span className="truncate">{r.headline}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
