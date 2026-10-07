import { useState } from 'react';
import { Loader2, Trash2, Users } from 'lucide-react';
import type { DomainDetail, User } from '../lib/api';
import { MembersDialog } from './MembersDialog';

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="text-right">
      <div className="font-mono text-sm text-ink">{value}</div>
      <div className="text-[10px] tracking-wider text-faint uppercase">{label}</div>
    </div>
  );
}

export function DomainHeader({ detail, me, onDelete, onLeft }: {
  detail: DomainDetail;
  me: User;
  onDelete: () => Promise<void>;
  onLeft: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sharing, setSharing] = useState(false);
  const { stats, role } = detail;

  return (
    <header className="flex items-center gap-6 border-b border-line px-5 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="flex items-center gap-2 truncate font-mono text-[15px] text-ink">
          {detail.domain}
          {role !== 'owner' && (
            <span className="rounded-full border border-line-2 px-2 py-px font-sans text-[10px] text-dim" title="Your role in this domain">{role}</span>
          )}
        </h1>
        <p className="truncate text-xs text-dim">{detail.config.description}</p>
      </div>

      {!detail.ontology_generated && (
        <span className="flex items-center gap-1.5 rounded-full border border-gold/30 bg-gold/10 px-2.5 py-1 text-[11px] text-gold">
          <Loader2 size={11} className="animate-spin" /> generating ontology
        </span>
      )}

      <div className="flex gap-5 max-sm:hidden">
        <Stat label="entities" value={stats.entity_count} />
        <Stat label="facts" value={stats.fact_count} />
        <Stat label="sources" value={stats.episode_count} />
        {stats.invalid_fact_count > 0 && <Stat label="superseded" value={stats.invalid_fact_count} />}
      </div>

      <button onClick={() => setSharing(true)} className="rounded p-1.5 text-faint hover:bg-panel-2 hover:text-ink" title="Members">
        <Users size={14} />
      </button>

      {role !== 'owner' ? null : confirm ? (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-dim">Delete domain and its graph?</span>
          <button
            disabled={busy}
            onClick={async () => { setBusy(true); try { await onDelete(); } finally { setBusy(false); setConfirm(false); } }}
            className="rounded bg-bad px-2 py-1 text-black disabled:opacity-50"
          >
            Delete
          </button>
          <button onClick={() => setConfirm(false)} className="px-1 text-faint hover:text-ink">Cancel</button>
        </div>
      ) : (
        <button onClick={() => setConfirm(true)} className="rounded p-1.5 text-faint hover:bg-panel-2 hover:text-bad" title="Delete domain">
          <Trash2 size={14} />
        </button>
      )}

      {sharing && (
        <MembersDialog domain={detail.domain} me={me} role={role} onClose={() => setSharing(false)}
          onLeft={async () => { setSharing(false); await onLeft(); }} />
      )}
    </header>
  );
}
