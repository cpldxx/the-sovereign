import { useState } from 'react';
import { can, type DomainDetail, type Ontology } from '../lib/api';
import { ActionsPanel } from './ActionsPanel';
import { LearningPanel } from './LearningPanel';
import { SubNav } from './BriefingPanel';
import { OntologyPanel } from './OntologyPanel';
import { ReviewPanel } from './ReviewPanel';

type View = 'learning' | 'playbooks' | 'sensors' | 'apis' | 'ontology' | 'review';

/** What you set up once and look at now and then: how the graph learns by itself, the playbooks, live-data sensors, connected APIs, the grammar
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
  const [view, setView] = useState<View>('learning');
  const reviews = detail.stats.pending_reviews;
  return (
    <div className="flex h-full flex-col">
      <SubNav value={view} onChange={setView} items={[
        ['learning', 'Learning'], ['playbooks', 'Playbooks'], ['sensors', 'Sensors'], ['apis', 'APIs'], ['ontology', 'Ontology'],
        ['review', reviews ? `Review · ${reviews}` : 'Review'],
      ]} />
      <div className="min-h-0 flex-1">
        {(view === 'playbooks' || view === 'sensors' || view === 'apis') && (
          <ActionsPanel key={view} domain={domain} role={detail.role} version={version} view={view === 'apis' ? 'catalog' : view}
            onSelect={onSelect} onHighlight={onHighlight} onPending={onPending} />
        )}
        {view === 'learning' && <LearningPanel domain={domain} editable={detail.role !== 'viewer'} />}
        {view === 'ontology' && ontology && (
          <OntologyPanel domain={domain} ontology={ontology} generated={detail.ontology_generated}
            counts={detail.stats.categories} editable={can(detail.role, 'editor')} onChanged={onOntologyChanged} />
        )}
        {view === 'review' && <ReviewPanel domain={domain} version={version} editable={can(detail.role, 'editor')} onChanged={onChanged} />}
      </div>
    </div>
  );
}
