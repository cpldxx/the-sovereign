import { can, type DomainDetail, type Ontology } from '../lib/api';
import { CharterPanel } from './CharterPanel';
import { LearningPanel } from './LearningPanel';
import { SubNav } from './BriefingPanel';
import { OntologyPanel } from './OntologyPanel';
import { ReviewPanel } from './ReviewPanel';

export type SettingsView = 'charter' | 'learning' | 'ontology' | 'review';

/** What you set up once and look at now and then: what you want from the domain (its research charter), how the graph
 * learns by itself, its grammar, and the Head's review decisions (to overrule one). Day to day, the Head runs all of it. */
export function SettingsPanel({ domain, detail, ontology, version, view, onView, onAskHead, onChanged, onOntologyChanged }: {
  domain: string;
  detail: DomainDetail;
  ontology: Ontology | null;
  version: number;
  view: SettingsView;
  onView: (view: SettingsView) => void;
  onAskHead: () => void;
  onChanged: () => Promise<void>;
  onOntologyChanged: () => Promise<void>;
}) {
  const reviews = detail.stats.pending_reviews;
  return (
    <div className="flex h-full flex-col">
      <SubNav value={view} onChange={onView} items={[
        ['charter', 'Charter'], ['learning', 'Learning'], ['ontology', 'Ontology'], ['review', reviews ? `Review · ${reviews}` : 'Review'],
      ]} />
      <div className="min-h-0 flex-1">
        {view === 'charter' && <CharterPanel key={version} domain={domain} editable={can(detail.role, 'editor')} onAskHead={onAskHead} />}
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
