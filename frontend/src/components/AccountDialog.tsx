import { useEffect, useState } from 'react';
import { Check, Copy, KeyRound, Link2, Loader2, Trash2, X } from 'lucide-react';
import { auth, KG_API, type ApiToken, type Invite, type User } from '../lib/api';

const INPUT = 'w-full rounded-md border border-line-2 bg-bg px-3 py-1.5 text-sm outline-none focus:border-gold/60';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line pt-4">
      <h3 className="mb-2 font-mono text-[10px] tracking-widest text-faint uppercase">{title}</h3>
      {children}
    </section>
  );
}

/** A secret shown once, with a copy button. */
function Secret({ value, note }: { value: string; note: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-2 rounded-md border border-gold/30 bg-gold/5 p-2.5">
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink">{value}</code>
        <button
          onClick={async () => { await navigator.clipboard.writeText(value); setCopied(true); }}
          className="rounded p-1 text-dim hover:text-ink" title="Copy"
        >
          {copied ? <Check size={13} className="text-good" /> : <Copy size={13} />}
        </button>
      </div>
      <p className="mt-1 text-[10.5px] text-faint">{note}</p>
    </div>
  );
}

function Password() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle');
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      onSubmit={async e => {
        e.preventDefault();
        setState('busy'); setError(null);
        try {
          await auth.changePassword(current, next);
          setCurrent(''); setNext(''); setState('done');
        } catch (err) {
          setError((err as Error).message); setState('idle');
        }
      }}
      className="space-y-2"
    >
      <input type="password" placeholder="Current password" value={current} onChange={e => setCurrent(e.target.value)}
        autoComplete="current-password" required className={INPUT} />
      <input type="password" placeholder="New password (10+ characters)" value={next} onChange={e => setNext(e.target.value)}
        autoComplete="new-password" required minLength={10} className={INPUT} />
      {error && <p className="text-xs text-bad">{error}</p>}
      <div className="flex items-center gap-3">
        <button disabled={state === 'busy'} className="rounded-md border border-line-2 px-3 py-1.5 text-xs text-dim hover:text-ink disabled:opacity-40">
          Change password
        </button>
        {state === 'done' && <span className="text-[11px] text-good">Changed — other browsers were signed out.</span>}
      </div>
    </form>
  );
}

function Tokens() {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [label, setLabel] = useState('');
  const [created, setCreated] = useState<ApiToken | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => auth.tokens().then(setTokens).catch(e => setError((e as Error).message));
  useEffect(() => { void load(); }, []);
  return (
    <div>
      <p className="mb-2 text-[11px] leading-relaxed text-dim">
        For MCP clients and scripts: <code className="font-mono text-[10.5px]">Authorization: Bearer &lt;token&gt;</code> on{' '}
        <code className="font-mono text-[10.5px]">{KG_API}/mcp/readonly</code> (or <code className="font-mono text-[10.5px]">/mcp</code> where you edit).
      </p>
      <form
        onSubmit={async e => {
          e.preventDefault();
          setError(null);
          try {
            setCreated(await auth.createToken(label));
            setLabel('');
            await load();
          } catch (err) {
            setError((err as Error).message);
          }
        }}
        className="flex gap-2"
      >
        <input placeholder="What will use it (e.g. Claude Desktop)" value={label} onChange={e => setLabel(e.target.value)}
          required className={INPUT} />
        <button className="shrink-0 rounded-md border border-line-2 px-3 text-xs text-dim hover:text-ink">Create</button>
      </form>
      {created?.token && <Secret value={created.token} note="Shown once — store it now. Revoke it here when it's no longer needed." />}
      {error && <p className="mt-2 text-xs text-bad">{error}</p>}
      <ul className="mt-2 space-y-1">
        {tokens?.map(t => (
          <li key={t.uid} className="flex items-center gap-2 text-xs">
            <KeyRound size={12} className="text-faint" />
            <span className="min-w-0 flex-1 truncate text-dim">{t.label}</span>
            <span className="font-mono text-[10px] text-faint">{t.created_at.slice(0, 10)}</span>
            <button onClick={async () => { await auth.revokeToken(t.uid); await load(); }}
              className="rounded p-1 text-faint hover:text-bad" title="Revoke"><Trash2 size={12} /></button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Invites() {
  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [signup, setSignup] = useState('invite');
  const [created, setCreated] = useState<Invite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => auth.invites().then(r => { setInvites(r.invites); setSignup(r.signup); })
    .catch(e => setError((e as Error).message));
  useEffect(() => { void load(); }, []);
  const link = (code: string) => `${location.origin}${location.pathname}#/invite/${code}`;
  const open = invites?.filter(i => !i.used_by && i.expires_at > new Date().toISOString()) ?? [];
  return (
    <div>
      <p className="mb-2 text-[11px] leading-relaxed text-dim">
        {signup === 'open'
          ? 'Sign-up is open (SIGNUP=open): anyone who can reach this app can create an account.'
          : 'Sign-up needs an invite: one link per person, valid 7 days. Pass it on yourself — nothing is emailed.'}
      </p>
      <button
        onClick={async () => {
          setError(null);
          try { setCreated(await auth.createInvite()); await load(); } catch (e) { setError((e as Error).message); }
        }}
        className="flex items-center gap-1.5 rounded-md border border-line-2 px-3 py-1.5 text-xs text-dim hover:text-ink"
      >
        <Link2 size={12} /> New invite link
      </button>
      {created?.code && <Secret value={link(created.code)} note="Shown once. Whoever opens it can create one account." />}
      {error && <p className="mt-2 text-xs text-bad">{error}</p>}
      {open.length > 0 && (
        <ul className="mt-2 space-y-1">
          {open.map(i => (
            <li key={i.uid} className="flex items-center gap-2 text-xs">
              <Link2 size={12} className="text-faint" />
              <span className="flex-1 text-dim">unused · expires {i.expires_at.slice(0, 10)}</span>
              <button onClick={async () => { await auth.revokeInvite(i.uid); await load(); }}
                className="rounded p-1 text-faint hover:text-bad" title="Revoke"><Trash2 size={12} /></button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AccountDialog({ user, onClose, onSignOut }: { user: User; onClose: () => void; onSignOut: () => Promise<void> }) {
  const [leaving, setLeaving] = useState(false);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="max-h-[90vh] w-full max-w-md space-y-4 overflow-y-auto rounded-lg border border-line-2 bg-panel p-5 shadow-2xl">
        <div className="flex items-start justify-between">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-medium">{user.name || user.email}</h2>
            <p className="truncate text-xs text-dim">{user.email}{user.admin && <span className="ml-2 text-gold">admin</span>}</p>
          </div>
          <button onClick={onClose} className="text-faint hover:text-ink"><X size={16} /></button>
        </div>
        <Section title="Password"><Password /></Section>
        <Section title="API tokens"><Tokens /></Section>
        {user.admin && <Section title="Invites"><Invites /></Section>}
        <div className="border-t border-line pt-4">
          <button
            disabled={leaving}
            onClick={async () => { setLeaving(true); await onSignOut(); }}
            className="flex items-center gap-2 rounded-md border border-line-2 px-3 py-1.5 text-xs text-dim hover:border-bad/50 hover:text-bad disabled:opacity-40"
          >
            {leaving && <Loader2 size={12} className="animate-spin" />} Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
