import { useState } from 'react';
import { X } from 'lucide-react';
import { DomainForm } from './DomainForm';

export function NewDomainDialog({ onCreate, onClose }: {
  onCreate: (name: string, description: string) => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}
    >
      <div className="w-full max-w-md rounded-lg border border-line-2 bg-panel p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-medium">New domain</h2>
          <button onClick={onClose} disabled={busy} className="text-faint hover:text-ink"><X size={16} /></button>
        </div>
        <DomainForm
          onBusy={setBusy}
          onCreate={async (name, description) => { await onCreate(name, description); onClose(); }}
        />
      </div>
    </div>
  );
}
