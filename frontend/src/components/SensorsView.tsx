import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Code2, Loader2, Play, Plus, Radio, Trash2 } from 'lucide-react';
import { kg, type Sensor, type SensorReading, type SensorRequest } from '../lib/api';

const ACTIVE = new Set(['queued', 'coding', 'testing']);

function SensorCard({ domain, sensor, onChanged }: { domain: string; sensor: Sensor; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [params, setParams] = useState<Record<string, string>>(
    () => Object.fromEntries(Object.entries(sensor.params).map(([k, v]) => [k, String(v.example ?? '')])));
  const [reading, setReading] = useState<SensorReading | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState<string | null>(null);

  async function read() {
    setBusy(true);
    try {
      const typed = Object.fromEntries(Object.entries(params).map(([k, v]) => {
        const t = sensor.params[k]?.type;
        return [k, t === 'number' || t === 'integer' ? Number(v) : t === 'boolean' ? v === 'true' : v];
      }));
      setReading(await kg.readSensor(domain, sensor.name, typed));
    } catch (e) {
      setReading({ sensor: sensor.name, params: {}, read_at: '', ok: false, error: (e as Error).message });
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
            <Radio size={12} className={sensor.last_ok ? 'text-good' : 'text-bad'} />
            <span className="font-mono text-[12px] text-ink">{sensor.name}</span>
            <span className="ml-auto text-[10px] text-faint">{sensor.author.replace('coder:', '')}</span>
          </div>
          <p className="mt-0.5 text-[11.5px] text-dim">{sensor.description}</p>
        </div>
      </button>
      {open && (
        <div className="mt-2 space-y-2 border-t border-line pt-2">
          <div className="flex flex-wrap items-end gap-2">
            {Object.entries(sensor.params).map(([k, v]) => (
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
            <pre className={`max-h-56 overflow-auto whitespace-pre-wrap rounded bg-bg p-2 font-mono text-[10.5px] ${reading.ok ? 'text-dim' : 'text-bad'}`}>
              {reading.ok ? JSON.stringify(reading.result, null, 1) : reading.error}
            </pre>
          )}
          <div className="flex gap-2">
            <button onClick={async () => setCode(code ? null : (await kg.sensor(domain, sensor.name)).code ?? '')}
              className="flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-ink">
              <Code2 size={10} /> {code ? 'Hide code' : 'Code'}
            </button>
            <button onClick={async () => { await kg.removeSensor(domain, sensor.name); await onChanged(); }}
              className="ml-auto flex items-center gap-1 rounded border border-line-2 px-2 py-0.5 text-[10.5px] text-dim hover:text-bad">
              <Trash2 size={10} /> Remove
            </button>
          </div>
          {code && <pre className="max-h-72 overflow-auto rounded bg-bg p-2 font-mono text-[10.5px] text-dim">{code}</pre>}
          <p className="text-[10px] text-faint">for: {sensor.need}</p>
        </div>
      )}
    </li>
  );
}

function RequestRow({ r }: { r: SensorRequest }) {
  const [open, setOpen] = useState(false);
  const active = ACTIVE.has(r.status);
  return (
    <li className="border-b border-line py-1.5 text-[11.5px]">
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-2 text-left">
        {active ? <Loader2 size={11} className="shrink-0 animate-spin text-gold" /> : open ? <ChevronDown size={11} className="shrink-0 text-faint" /> : <ChevronRight size={11} className="shrink-0 text-faint" />}
        <span className="truncate text-dim">{r.need}</span>
        <span className={`ml-auto shrink-0 font-mono text-[10px] ${r.status === 'failed' ? 'text-bad' : r.status === 'done' ? 'text-good' : 'text-gold'}`}>
          {r.status}{r.seconds ? ` · ${Math.round(r.seconds / 60)}m` : ''} · {r.backend}
        </span>
      </button>
      {open && (
        <div className="space-y-1 py-1 pl-5 text-[10.5px]">
          {r.error && <p className="text-bad">{r.error}</p>}
          {r.note && <p className="text-faint">{r.note}</p>}
          <ul className="font-mono text-faint">{r.log.map((l, i) => <li key={i} className="truncate">{l}</li>)}</ul>
        </div>
      )}
    </li>
  );
}

export function SensorsView({ domain, version }: { domain: string; version: number }) {
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [requests, setRequests] = useState<SensorRequest[]>([]);
  const [need, setNeed] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await kg.sensors(domain);
      setSensors(r.sensors);
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
        Live data the Head Agent checks right before acting — the graph can be a day old. The Coder Agent writes each
        sensor, tests it, and it always runs in a locked sandbox (public internet only).
      </p>
      <form onSubmit={submit} className="flex gap-2">
        <input value={need} onChange={e => setNeed(e.target.value)} placeholder="New sensor, e.g. latest stock price for a ticker"
          className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-3 py-1.5 text-[12px] outline-none focus:border-gold/60" />
        <button type="submit" disabled={need.trim().length < 5} className="flex items-center gap-1 rounded-md bg-gold px-2.5 text-[11.5px] text-black disabled:opacity-40">
          <Plus size={12} /> Ask the Coder
        </button>
      </form>
      {error && <p className="text-xs text-bad">{error}</p>}
      {sensors.length === 0 && <p className="text-center text-xs text-faint">No sensors yet.</p>}
      <ul className="space-y-2">{sensors.map(s => <SensorCard key={s.uid} domain={domain} sensor={s} onChanged={load} />)}</ul>
      {requests.length > 0 && (
        <section>
          <h4 className="mb-1 font-mono text-[10px] tracking-widest text-faint uppercase">Coder requests</h4>
          <ul>{requests.map(r => <RequestRow key={r.id} r={r} />)}</ul>
        </section>
      )}
    </div>
  );
}
