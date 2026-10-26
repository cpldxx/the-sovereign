import { can, type Role } from '../lib/api';
import { ReportPanel } from './ReportPanel';

/** What happened: the briefing each learning cycle writes — what the graph learned, what changed, what is unsure. */
export function BriefingPanel({ domain, role, version, onHighlight, onSelect }: {
  domain: string;
  role: Role;
  version: number;
  onHighlight: (uids: Set<string>) => void;
  onSelect: (uid: string) => void;
}) {
  return <ReportPanel domain={domain} version={version} editable={can(role, 'editor')} onHighlight={onHighlight} onSelect={onSelect} />;
}

/** A row of quiet sub-views under a tab. */
export function SubNav<T extends string>({ items, value, onChange }: {
  items: [T, string][];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-line px-2 py-1.5">
      {items.map(([id, label]) => (
        <button key={id} onClick={() => onChange(id)}
          className={`rounded-md px-2.5 py-1 text-[11.5px] ${value === id ? 'bg-panel-2 text-ink' : 'text-faint hover:text-dim'}`}>
          {label}
        </button>
      ))}
    </div>
  );
}
