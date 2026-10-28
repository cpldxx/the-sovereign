import { useState } from 'react';
import { can, type Role } from '../lib/api';
import { GrowthPanel } from './GrowthPanel';
import { ReportPanel } from './ReportPanel';

/** What happened: the briefing each learning cycle writes — what the graph learned, what changed, what is unsure — and
 * how the domain grows: the charter's exam, sat after every cycle. */
export function BriefingPanel({ domain, role, version, onHighlight, onSelect }: {
  domain: string;
  role: Role;
  version: number;
  onHighlight: (uids: Set<string>) => void;
  onSelect: (uid: string) => void;
}) {
  const [view, setView] = useState<'reports' | 'growth'>('reports');
  return (
    <div className="flex h-full flex-col">
      <SubNav value={view} onChange={setView} items={[['reports', 'Reports'], ['growth', 'Growth']]} />
      <div className="min-h-0 flex-1">
        {view === 'reports'
          ? <ReportPanel domain={domain} version={version} editable={can(role, 'editor')} onHighlight={onHighlight} onSelect={onSelect} />
          : <GrowthPanel domain={domain} version={version} editable={can(role, 'editor')} />}
      </div>
    </div>
  );
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
