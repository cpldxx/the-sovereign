import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Code2, Globe, Loader2, Play, Plus, Radio, Search, Server, Trash2 } from 'lucide-react';
import { kg, type ScoutCandidate, type SensorGroup, type SensorReading, type SensorRequest, type SensorSource } from '../lib/api';

const ACTIVE = new Set(['queued', 'planning', 'collecting', 'probing', 'coding', 'testing']);

function SourceRow({ domain, source, onChanged }: { domain: string; source: SensorSource; onChanged: () => Promise<void> }) {
  const [code, setCode] = useState<string | null>(null);
  const Icon = source.kind === 'page' ? Globe : Server;
  return (
    <li className="text-[11px]">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${source.last_ok === false ? 'bg-bad' : source.last_ok ? 'bg-good' : 'bg-faint'}`} />
        <Icon size={11} className="shrink-0 text-faint" />
        <span className="min-w-0 flex-1 truncate font-mono text-dim" title={source.last_error ?? source.name}>{source.host || source.name}</span>
        <span className="shrink-0 font-mono text-[10px] text-faint" title="recent success rate · average seconds">
          {Math.round(source.score * 100)}%{source.avg_seconds != null ? ` · ${source.avg_seconds}s` : ''}
        </span>
        <button onClick={async () => setCode(code ? null : (await kg.sensor(domain, source.name)).code ?? '')}
          className="rounded p-0.5 text-faint hover:text-ink" title="Code"><Code2 size={11} /></button>
        <button onClick={async () => { await kg.removeSensor(domain, source.name); await onChanged(); }}
          className="rounded p-0.5 text-faint hover:text-bad" title="Remove this source"><Trash2 size={11} /></button>
      </div>
      {source.last_ok === false && source.last_error && <p className="truncate pl-5 text-[10px] text-bad/80">{source.last_error}</p>}
      {code && <pre className="mt-1 max-h-60 overflow-auto rounded bg-bg p-2 font-mono text-[10.5px] text-dim">{code}</pre>}
    </li>
  );
}

function SensorCard({ domain, group, onChanged }: { domain: string; group: SensorGroup; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [params, setParams] = useState<Record<string, string>>(
    () => Object.fromEntries(Object.entries(group.params).map(([k, v]) => [k, String(v.example ?? '')])));
  const [reading, setReading] = useState<SensorReading | null>(null);
  const [busy, setBusy] = useState(false);

  async function read() {
    setBusy(true);
    try {
      const typed = Object.fromEntries(Object.entries(params).map(([k, v]) => {
        const t = group.params[k]?.type;
        return [k, t === 'number' || t === 'integer' ? Number(v) : t === 'boolean' ? v === 'true' : v];
      }));
      setReading(await kg.readSensor(domain, group.name, typed));
      await onChanged();
    } catch (e) {
      setReading({ sensor: group.name, params: {}, read_at: '', ok: false, error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-md border border-line bg-panel-2/40 p-3">
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-start gap-2 text-left">
        {open ? <ChevronDown size={13} className="mt-0.5 shrink-0 text-faint" /> : <ChevronRight size={13} className="mt-0.5 shrink-0 text-faint" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Radio size={12} className={group.last_ok ? 'text-good' : 'text-bad'} />
            <span className="truncate font-mono text-[12px] text-ink">{group.name}</span>
            <span className="ml-auto shrink-0 text-[10px] text-faint" title="sources that answered on their last reading">
              {group.working}/{group.sources.length} sources
            </span>
          </div>
          <p className="mt-0.5 text-[11.5px] text-dim">{group.description}</p>
        </div>
      </button>
      {open && (
        <div className="mt-2 space-y-2 border-t border-line pt-2">
          <div className="flex flex-wrap items-end gap-2">
            {Object.entries(group.params).map(([k, v]) => (
              <label key={k} className="text-[10.5px] text-faint" title={v.description}>
                {k}
                <input value={params[k] ?? ''} onChange={e => setParams({ ...params, [k]: e.target.value })}
                  className="mt-0.5 block w-32 rounded border border-line-2 bg-bg px-2 py-1 font-mono text-[11.5px] text-ink outline-none focus:border-gold/60" />
              </label>
            ))}
            <button onClick={read} disabled={busy}
              className="flex items-center gap-1 rounded-md bg-gold px-2.5 py-1 text-[11.5px] text-black disabled:opacity-40">
              {busy ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Read now
            </button>
          </div>
          {reading && (
            <div>
              {reading.source && <p className="mb-1 text-[10px] text-faint">from {reading.source}
                {reading.failed_sources?.length ? ` (after ${reading.failed_sources.map(f => f.source).join(', ')} failed)` : ''}</p>}
              <pre className={`max-h-56 overflow-auto whitespace-pre-wrap rounded bg-bg p-2 font-mono text-[10.5px] ${reading.ok ? 'text-dim' : 'text-bad'}`}>
                {reading.ok ? JSON.stringify(reading.result, null, 1) : reading.error}
              </pre>
            </div>
          )}
          <div>
            <h5 className="mb-1 font-mono text-[9.5px] tracking-widest text-faint uppercase">Sources — read best first, next on failure</h5>
            <ul className="space-y-1">{group.sources.map(s => <SourceRow key={s.name} domain={domain} source={s} onChanged={onChanged} />)}</ul>
          </div>
          <div className="flex gap-2">
            <button
              onClick={async () => { await kg.requestSensor(domain, group.need, 'scout', group.name); await onChanged(); }}
              className="flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-ink"
              title="The Scout tries 20-30 more sites and adds every one that works and agrees">
              <Search size={10} /> Find more sources
            </button>
            <button onClick={async () => { await kg.removeSensor(domain, group.name); await onChanged(); }}
              className="ml-auto flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-bad">
              <Trash2 size={10} /> Remove sensor
            </button>
          </div>
          <p className="text-[10px] text-faint">for: {group.need}</p>
        </div>
      )}
    </li>
  );
}

function CandidateRow({ c }: { c: ScoutCandidate }) {
  const color = c.outcome === 'works' ? 'text-good' : c.outcome === 'queued' || c.outcome === 'reachable' ? 'text-faint' : 'text-dim';
  return (
    <li className="flex gap-2">
      <span className="w-36 shrink-0 truncate text-faint" title={c.url}>{c.kind === 'page' ? '◻' : '⚙'} {c.host}</span>
      <span className={`min-w-0 flex-1 truncate ${color}`} title={c.outcome}>
        {c.outcome}{c.value ? ` ${Object.values(c.value).join(' / ')}` : ''}
      </span>
    </li>
  );
}

function RequestRow({ r }: { r: SensorRequest }) {
  const [open, setOpen] = useState(false);
  const active = ACTIVE.has(r.status);
  const works = r.candidates?.filter(c => c.outcome === 'works').length ?? 0;
  return (
    <li className="border-b border-line py-1.5 text-[11.5px]">
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-2 text-left">
        {active ? <Loader2 size={11} className="shrink-0 animate-spin text-gold" /> : open ? <ChevronDown size={11} className="shrink-0 text-faint" /> : <ChevronRight size={11} className="shrink-0 text-faint" />}
        <span className="truncate text-dim">{r.group ? `more sources for ${r.group}` : r.need}</span>
        <span className={`ml-auto shrink-0 font-mono text-[10px] ${r.status === 'failed' ? 'text-bad' : r.status === 'done' ? 'text-good' : 'text-gold'}`}>
          {r.status}{r.candidates?.length ? ` · ${works}/${r.candidates.length}` : ''}{r.seconds ? ` · ${Math.round(r.seconds / 60)}m` : ''} · {r.backend}
        </span>
      </button>
      {open && (
        <div className="space-y-1 py-1 pl-5 text-[10.5px]">
          {r.error && <p className="text-bad">{r.error}</p>}
          {r.note && <p className="text-faint">{r.note}</p>}
          {r.candidates && r.candidates.length > 0 && (
            <ul className="font-mono">{r.candidates.map(c => <CandidateRow key={c.host} c={c} />)}</ul>
          )}
          <ul className="font-mono text-faint">{r.log.map((l, i) => <li key={i} className="truncate">{l}</li>)}</ul>
        </div>
      )}
    </li>
  );
}

export function SensorsView({ domain, version }: { domain: string; version: number }) {
  const [groups, setGroups] = useState<SensorGroup[]>([]);
  const [requests, setRequests] = useState<SensorRequest[]>([]);
  const [need, setNeed] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await kg.sensors(domain);
      setGroups(r.groups);
      setRequests(r.requests);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [domain]);

  useEffect(() => { void load(); }, [load, version]);
  const anyActive = requests.some(r => ACTIVE.has(r.status));
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [anyActive, load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await kg.requestSensor(domain, need.trim());
      setNeed('');
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-[11px] leading-snug text-faint">
        Live data the Head Agent checks right before acting — the graph can be a day old. Each sensor reads from many
        sources (APIs, and web pages opened in a browser) and falls back to the next one when a source fails. The Scout
        finds them; every one runs in a locked sandbox (public internet only).
      </p>
      <form onSubmit={submit} className="flex gap-2">
        <input value={need} onChange={e => setNeed(e.target.value)} placeholder="New sensor, e.g. latest stock price for a ticker"
          className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-3 py-1.5 text-[12px] outline-none focus:border-gold/60" />
        <button type="submit" disabled={need.trim().length < 5} className="flex items-center gap-1 rounded-md bg-gold px-2.5 text-[11.5px] text-black disabled:opacity-40">
          <Plus size={12} /> Find sources
        </button>
      </form>
      {error && <p className="text-xs text-bad">{error}</p>}
      {groups.length === 0 && <p className="text-center text-xs text-faint">No sensors yet.</p>}
      <ul className="space-y-2">{groups.map(g => <SensorCard key={g.name} domain={domain} group={g} onChanged={load} />)}</ul>
      {requests.length > 0 && (
        <section>
          <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Sensor requests</h4>
          <ul>{requests.map(r => <RequestRow key={r.id} r={r} />)}</ul>
        </section>
      )}
    </div>
  );
}
