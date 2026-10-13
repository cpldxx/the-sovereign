import { useState } from 'react';
import type { DomainDetail, Ontology } from '../lib/api';
import { ActionsPanel } from './ActionsPanel';
import { SubNav } from './BriefingPanel';
import { OntologyPanel } from './OntologyPanel';
import { ReviewPanel } from './ReviewPanel';

type View = 'playbooks' | 'sensors' | 'apis' | 'ontology' | 'review';

/** What you set up once and look at now and then: the playbooks, live-data sensors, connected APIs, the grammar
 * of the graph, and the Head's review decisions (to overrule one). Day to day, the Head runs all of it. */
export function SettingsPanel({ domain, detail, ontology, version, onSelect, onHighlight, onPending, onChanged, onOntologyChanged }: {
  domain: string;
  detail: DomainDetail;
  ontology: Ontology | null;
  version: number;
  onSelect: (uid: string) => void;
  onHighlight: (uids: Set<string>) => void;
  onPending: (count: number) => void;
  onChanged: () => Promise<void>;
  onOntologyChanged: () => Promise<void>;
}) {
  const [view, setView] = useState<View>('playbooks');
  const reviews = detail.stats.pending_reviews;
  return (
    <div className="flex h-full flex-col">
      <SubNav value={view} onChange={setView} items={[
        ['playbooks', 'Playbooks'], ['sensors', 'Sensors'], ['apis', 'APIs'], ['ontology', 'Ontology'],
        ['review', reviews ? `Review · ${reviews}` : 'Review'],
      ]} />
      <div className="min-h-0 flex-1">
        {(view === 'playbooks' || view === 'sensors' || view === 'apis') && (
          <ActionsPanel key={view} domain={domain} version={version} view={view === 'apis' ? 'catalog' : view}
            onSelect={onSelect} onHighlight={onHighlight} onPending={onPending} />
        )}
        {view === 'ontology' && ontology && (
          <OntologyPanel domain={domain} ontology={ontology} generated={detail.ontology_generated}
            counts={detail.stats.categories} onChanged={onOntologyChanged} />
        )}
        {view === 'review' && <ReviewPanel domain={domain} version={version} onChanged={onChanged} />}
      </div>
    </div>
  );
}
