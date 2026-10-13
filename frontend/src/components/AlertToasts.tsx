import { useEffect, useRef, useState } from 'react';
import { Bell, X } from 'lucide-react';
import { kg, type Proposal } from '../lib/api';

/**
 * Where an alert goes: a pop-up in this window the moment it runs (the Head's or a playbook's), a Mac notification
 * too when you turned that on (Briefing › What was done), and the list there. Nothing leaves this machine — to get
 * alerts elsewhere (Slack, Telegram, mail), connect that API under Settings › APIs and let the Head use it.
 */
export const NOTIFY_KEY = 'sovereign.notify';

export function notifyOnMac(): boolean {
  try { return localStorage.getItem(NOTIFY_KEY) === '1' && 'Notification' in window && Notification.permission === 'granted'; }
  catch { return false; }
}

const text = (v: unknown) => (typeof v === 'string' ? v : '');

export function AlertToasts({ domain, onOpen }: { domain: string; onOpen: () => void }) {
  const [toasts, setToasts] = useState<Proposal[]>([]);
  const seen = useRef<Set<string> | null>(null);

  useEffect(() => {
    let alive = true;
    seen.current = null;
    const poll = async () => {
      const done = await kg.proposals(domain, 'executed', 20).catch(() => null);
      if (!alive || !done) return;
      if (seen.current === null) {   // what ran before this window opened isn't news
        seen.current = new Set(done.map(p => p.uid));
        return;
      }
      const fresh = done.filter(p => !seen.current!.has(p.uid) && p.action === 'alert');
      done.forEach(p => seen.current!.add(p.uid));
      if (!fresh.length) return;
      setToasts(t => [...fresh, ...t].slice(0, 3));
      if (notifyOnMac()) {
        for (const p of fresh) new Notification(`Sovereign · ${text(p.params.title) || 'Alert'}`, { body: text(p.params.message), tag: p.uid });
      }
    };
    void poll();
    const t = setInterval(poll, 8000);
    return () => { alive = false; clearInterval(t); };
  }, [domain]);

  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-50 w-[340px] space-y-2 lg:right-[456px] lg:bottom-6">
      {toasts.map(p => (
        <div key={p.uid} className="pointer-events-auto rounded-md border border-line-2 bg-panel-2 p-3 shadow-2xl shadow-black/60">
          <div className="flex items-start gap-2">
            <Bell size={13} className="mt-0.5 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="text-[12.5px] text-ink">{text(p.params.title) || 'Alert'}</div>
              {text(p.params.message) && <p className="mt-0.5 line-clamp-3 text-[11.5px] leading-snug text-dim">{text(p.params.message)}</p>}
              <div className="mt-1.5 flex gap-3 text-[10.5px] text-faint">
                <span>from {p.source === 'playbook' ? 'a playbook' : 'the Head'}</span>
                <button onClick={() => { onOpen(); setToasts(t => t.filter(x => x.uid !== p.uid)); }} className="underline hover:text-ink">open</button>
              </div>
            </div>
            <button onClick={() => setToasts(t => t.filter(x => x.uid !== p.uid))} className="text-faint hover:text-ink" title="Dismiss"><X size={12} /></button>
          </div>
        </div>
      ))}
    </div>
  );
}

/** The switch for Mac notifications (the browser asks for permission once). */
export function MacNotifySwitch() {
  const [on, setOn] = useState(notifyOnMac);
  const supported = 'Notification' in window;
  async function toggle() {
    if (on) {
      try { localStorage.setItem(NOTIFY_KEY, '0'); } catch { /* per-viewer convenience */ }
      setOn(false);
      return;
    }
    const permission = await Notification.requestPermission();
    try { localStorage.setItem(NOTIFY_KEY, permission === 'granted' ? '1' : '0'); } catch { /* per-viewer convenience */ }
    setOn(permission === 'granted');
  }
  if (!supported) return null;
  return (
    <button onClick={toggle} className={`flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10.5px] ${on ? 'border-accent/60 text-accent' : 'border-line-2 text-dim hover:text-ink'}`}
      title="Alerts also as macOS notifications while Sovereign is open in a browser tab">
      <Bell size={10} /> {on ? 'Mac notifications on' : 'Also notify me on this Mac'}
    </button>
  );
}
