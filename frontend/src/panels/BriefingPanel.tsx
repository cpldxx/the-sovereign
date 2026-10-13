import { ActionsPanel } from './ActionsPanel';
import { MacNotifySwitch } from '../components/AlertToasts';
import { ReportPanel } from './ReportPanel';

export type BriefingView = 'report' | 'activity';

/** What happened: the daily report, and what was done (alerts, drafts, the APIs run and what they returned). */
export function BriefingPanel({ domain, version, view, onView, onHighlight, onSelect, onPending }: {
  domain: string;
  version: number;
  view: BriefingView;
  onView: (v: BriefingView) => void;
  onHighlight: (uids: Set<string>) => void;
  onSelect: (uid: string) => void;
  onPending: (count: number) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <SubNav items={[['report', 'Report'], ['activity', 'What was done']]} value={view} onChange={onView} />
      {view === 'activity' && (
        <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-[10.5px] leading-snug text-faint">
          <span className="flex-1">An alert pops up in this window when it runs and stays listed here. Nothing is sent
            off this Mac — connect Slack, Telegram or mail under Settings › APIs to get alerts there.</span>
          <MacNotifySwitch />
        </div>
      )}
      <div className="min-h-0 flex-1">
        {view === 'report'
          ? <ReportPanel domain={domain} version={version} onHighlight={onHighlight} onSelect={onSelect} />
          : <ActionsPanel domain={domain} version={version} view="inbox" onSelect={onSelect} onHighlight={onHighlight} onPending={onPending} />}
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
