import { useState } from 'react';
import { Loader2 } from 'lucide-react';

/** Display name → the id the server will derive (mirrors kg/domains/registry.py:domain_id). */
export function previewId(name: string): string {
  if (!name.trim()) return '';
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  // No Latin letter first ("반도체", "2026 plan"): the server names it "domain"; the title stays as typed.
  return /^[a-z]/.test(slug) ? slug : 'domain';
}

export function DomainForm({ onCreate, onBusy }: {
  onCreate: (name: string, description: string) => Promise<void>;
  onBusy?: (busy: boolean) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = previewId(name);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !description.trim()) return;
    setBusy(true); onBusy?.(true); setError(null);
    try {
      await onCreate(name.trim(), description.trim());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false); onBusy?.(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <label className="block">
        <span className="text-[11px] text-dim">Name</span>
        <input
          autoFocus
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Alzheimer's treatments"
          className="mt-1 w-full rounded-md border border-line-2 bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60"
        />
        {id && <span className="mt-1 block font-mono text-[10.5px] text-faint">id: {id} (+ a short suffix if another account has it)</span>}
      </label>
      <label className="block">
        <span className="text-[11px] text-dim">What is this domain about?</span>
        <textarea
          value={description}
          onChange={e => setDescription(e.target.value)}
          rows={3}
          placeholder="New treatments for Alzheimer's disease: antibodies, clinical trials, biomarkers"
          className="mt-1 w-full resize-none rounded-md border border-line-2 bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60"
        />
        <span className="mt-1 block text-[10.5px] text-faint">
          A first sketch is enough. Next, the Head asks what you really want to find out and writes a research
          charter with you; research starts when you agree.
        </span>
      </label>
      {error && <p className="text-xs text-bad">{error}</p>}
      <button
        type="submit"
        disabled={busy || !id || !description.trim()}
        className="flex w-full items-center justify-center gap-2 rounded-md bg-accent py-2 text-sm font-medium text-black disabled:opacity-40"
      >
        {busy && <Loader2 size={14} className="animate-spin" />} Create domain
      </button>
    </form>
  );
}
