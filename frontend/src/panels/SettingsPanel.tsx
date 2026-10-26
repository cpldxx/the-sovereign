import { useState } from 'react';
import { can, type DomainDetail, type Ontology } from '../lib/api';
import { LearningPanel } from './LearningPanel';
import { SubNav } from './BriefingPanel';
import { OntologyPanel } from './OntologyPanel';
import { ReviewPanel } from './ReviewPanel';

type View = 'learning' | 'ontology' | 'review';

/** What you set up once and look at now and then: how the graph learns by itself, its grammar, and the Head's review
 * decisions (to overrule one). Day to day, the Head runs all of it. */
export function SettingsPanel({ domain, detail, ontology, version, onChanged, onOntologyChanged }: {
  domain: string;
  detail: DomainDetail;
  ontology: Ontology | null;
  version: number;
  onChanged: () => Promise<void>;
  onOntologyChanged: () => Promise<void>;
}) {
  const [view, setView] = useState<View>('learning');
  const reviews = detail.stats.pending_reviews;
  return (
    <div className="flex h-full flex-col">
      <SubNav value={view} onChange={setView} items={[
        ['learning', 'Learning'], ['ontology', 'Ontology'], ['review', reviews ? `Review · ${reviews}` : 'Review'],
      ]} />
      <div className="min-h-0 flex-1">
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
