import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { auth, type User } from '../lib/api';

const INPUT = 'mt-1 w-full rounded-md border border-line-2 bg-bg px-3 py-2 text-sm outline-none focus:border-gold/60';

/** Sign in, or sign up (with the invite code from an invite link: #/invite/<code>). */
export function SignIn({ invite, onSignedIn }: { invite: string | null; onSignedIn: (user: User) => void }) {
  const [config, setConfig] = useState<{ signup: string; first_account: boolean } | null>(null);
  const [mode, setMode] = useState<'in' | 'up'>(invite ? 'up' : 'in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [code, setCode] = useState(invite ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    auth.config().then(c => {
      setConfig(c);
      if (c.first_account) setMode('up');
    }).catch(e => setError((e as Error).message));
  }, []);

  const first = !!config?.first_account;
  const needsInvite = mode === 'up' && !first && config?.signup !== 'open';

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = mode === 'in'
        ? await auth.login(email, password)
        : await auth.signup(email, password, name, code);
      if (invite) history.replaceState(null, '', '#/');
      onSignedIn(user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-1 font-mono text-[11px] tracking-[0.3em] text-gold">THE SOVEREIGN</div>
        <h1 className="mb-2 text-xl font-medium">
          {first ? 'Create the first account' : mode === 'in' ? 'Sign in' : 'Create your account'}
        </h1>
        <p className="mb-5 text-sm leading-relaxed text-dim">
          {first
            ? 'The first account is the admin: it owns the domains that already exist and invites everyone else.'
            : mode === 'in'
              ? 'Your domains, their knowledge graphs and the Head Agent.'
              : 'Each account sees only its own domains and the ones shared with it.'}
        </p>

        <form onSubmit={submit} className="space-y-3 rounded-lg border border-line bg-panel p-5">
          {mode === 'up' && (
            <label className="block">
              <span className="text-[11px] text-dim">Name</span>
              <input value={name} onChange={e => setName(e.target.value)} autoComplete="name" className={INPUT} />
            </label>
          )}
          <label className="block">
            <span className="text-[11px] text-dim">Email</span>
            <input type="email" required autoFocus value={email} onChange={e => setEmail(e.target.value)}
              autoComplete="email" className={INPUT} />
          </label>
          <label className="block">
            <span className="text-[11px] text-dim">Password</span>
            <input type="password" required minLength={mode === 'up' ? 10 : undefined} value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete={mode === 'in' ? 'current-password' : 'new-password'} className={INPUT} />
            {mode === 'up' && <span className="mt-1 block text-[10.5px] text-faint">At least 10 characters.</span>}
          </label>
          {needsInvite && (
            <label className="block">
              <span className="text-[11px] text-dim">Invite code</span>
              <input required value={code} onChange={e => setCode(e.target.value)} className={`${INPUT} font-mono`} />
              <span className="mt-1 block text-[10.5px] text-faint">From the invite link an admin gave you.</span>
            </label>
          )}
          {error && <p className="text-xs text-bad">{error}</p>}
          <button type="submit" disabled={busy || !config}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-gold py-2 text-sm font-medium text-black disabled:opacity-40">
            {busy && <Loader2 size={14} className="animate-spin" />} {mode === 'in' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        {!first && config && (
          <p className="mt-4 text-center text-xs text-faint">
            {mode === 'in' ? 'No account yet? ' : 'Already have an account? '}
            <button onClick={() => { setMode(mode === 'in' ? 'up' : 'in'); setError(null); }}
              className="text-dim underline-offset-2 hover:text-ink hover:underline">
              {mode === 'in' ? 'Create one' : 'Sign in'}
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
