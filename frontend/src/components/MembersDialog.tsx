import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { kg, type Member, type Role, type User } from '../lib/api';

const ROLE_HINT: Record<Role, string> = {
  viewer: 'reads the graph, reports and actions; asks the Head (read-only tools)',
  editor: 'also ingests, researches, reviews, edits the ontology and confirms actions',
  owner: 'also shares the domain, configures webhooks and deletes it',
};
const ROLES: Role[] = ['viewer', 'editor', 'owner'];

/** Who can use a domain. Owners add existing accounts by email and change or remove roles; anyone can leave. */
export function MembersDialog({ domain, me, role, onClose, onLeft }: {
  domain: string;
  me: User;
  role: Role;
  onClose: () => void;
  onLeft: () => Promise<void>;
}) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [email, setEmail] = useState('');
  const [newRole, setNewRole] = useState<Role>('viewer');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const owner = role === 'owner';

  useEffect(() => { kg.members(domain).then(setMembers).catch(e => setError((e as Error).message)); }, [domain]);

  async function run(action: () => Promise<Member[] | void>) {
    setBusy(true); setError(null);
    try {
      const next = await action();
      if (next) setMembers(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}
    >
      <div className="w-full max-w-md rounded-lg border border-line-2 bg-panel p-5 shadow-2xl">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-sm font-medium">Members of <span className="font-mono">{domain}</span></h2>
          <button onClick={onClose} className="text-faint hover:text-ink"><X size={16} /></button>
        </div>
        <p className="mb-4 text-[11px] text-faint">Everyone here sees the same graph, reports, playbooks and actions.</p>

        <ul className="mb-4 space-y-1.5">
          {members === null && <li className="text-xs text-faint">Loading…</li>}
          {members?.map(m => (
            <li key={m.uid} className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs text-ink">{m.name || m.email}{m.uid === me.uid && <span className="text-faint"> (you)</span>}</div>
                {m.name && <div className="truncate text-[10.5px] text-faint">{m.email}</div>}
              </div>
              {owner ? (
                <select
                  value={m.role}
                  disabled={busy}
                  onChange={e => run(() => kg.share(domain, m.email, e.target.value as Role))}
                  className="rounded border border-line-2 bg-bg px-1.5 py-1 text-[11px] text-dim"
                >
                  {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              ) : (
                <span className="text-[11px] text-dim">{m.role}</span>
              )}
              {(owner || m.uid === me.uid) && (
                <button
                  disabled={busy}
                  onClick={() => run(async () => {
                    const next = await kg.unshare(domain, m.uid);
                    if (m.uid === me.uid) { await onLeft(); return; }
                    return next;
                  })}
                  className="rounded px-1.5 py-1 text-[11px] text-faint hover:text-bad"
                >
                  {m.uid === me.uid ? 'Leave' : 'Remove'}
                </button>
              )}
            </li>
          ))}
        </ul>

        {owner && (
          <form
            onSubmit={e => { e.preventDefault(); void run(async () => { const next = await kg.share(domain, email, newRole); setEmail(''); return next; }); }}
            className="space-y-2 border-t border-line pt-4"
          >
            <div className="flex gap-2">
              <input
                type="email" required placeholder="Their account's email" value={email} onChange={e => setEmail(e.target.value)}
                className="min-w-0 flex-1 rounded-md border border-line-2 bg-bg px-3 py-1.5 text-sm outline-none focus:border-gold/60"
              />
              <select value={newRole} onChange={e => setNewRole(e.target.value as Role)}
                className="rounded-md border border-line-2 bg-bg px-2 text-xs text-dim">
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              <button disabled={busy} className="flex items-center gap-1.5 rounded-md bg-gold px-3 text-xs font-medium text-black disabled:opacity-40">
                {busy && <Loader2 size={12} className="animate-spin" />} Share
              </button>
            </div>
            <p className="text-[10.5px] text-faint">{newRole}: {ROLE_HINT[newRole]}. They need an account first.</p>
          </form>
        )}
        {error && <p className="mt-2 text-xs text-bad">{error}</p>}
      </div>
    </div>
  );
}
