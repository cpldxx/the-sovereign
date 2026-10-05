import { useCallback, useEffect, useState } from 'react';
import { Bot, FileInput, Globe, Inbox, Network, Search, Shapes } from 'lucide-react';
import { kg, research, type DomainDetail, type DomainSummary, type Graph, type Ontology } from './lib/api';
import { Sidebar } from './components/Sidebar';
import { DomainHeader } from './components/DomainHeader';
import { GraphView } from './components/GraphView';
import { EntityPanel } from './components/EntityPanel';
import { Welcome } from './components/Welcome';
import { AskPanel } from './panels/AskPanel';
import { IngestPanel } from './panels/IngestPanel';
import { SearchPanel } from './panels/SearchPanel';
import { OntologyPanel } from './panels/OntologyPanel';
import { ReviewPanel } from './panels/ReviewPanel';
import { ResearchPanel } from './panels/ResearchPanel';

type Tab = 'ask' | 'research' | 'ingest' | 'search' | 'review' | 'ontology';

const TABS: { id: Tab; label: string; icon: typeof Bot }[] = [
  { id: 'ask', label: 'Head', icon: Bot },
  { id: 'research', label: 'Research', icon: Globe },
  { id: 'ingest', label: 'Ingest', icon: FileInput },
  { id: 'search', label: 'Search', icon: Search },
  { id: 'review', label: 'Review', icon: Inbox },
  { id: 'ontology', label: 'Ontology', icon: Shapes },
];

const EMPTY_GRAPH: Graph = { entities: [], facts: [] };

/** Active domain lives in the URL hash (#/quant_trading) so links and reloads keep it. */
function readHash(): string | null {
  const id = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  return id || null;
}

export default function App() {
  const [domains, setDomains] = useState<DomainSummary[] | null>(null);
  const [active, setActive] = useState<string | null>(readHash);
  const [detail, setDetail] = useState<DomainDetail | null>(null);
  const [ontology, setOntology] = useState<Ontology | null>(null);
  const [graph, setGraph] = useState<Graph>(EMPTY_GRAPH);
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('ask');
  const [version, setVersion] = useState(0);  // bumps on every reload, so open panels refetch
  const [error, setError] = useState<string | null>(null);

  const loadDomains = useCallback(async () => {
    try {
      setDomains(await kg.domains());
      setError(null);
    } catch (e) {
      setError(String((e as Error).message));
      setDomains(d => d ?? []);
    }
  }, []);

  const loadDomain = useCallback(async (id: string) => {
    try {
      const [det, ont, g] = await Promise.all([kg.domain(id), kg.ontology(id), kg.graph(id)]);
      setDetail(det);
      setOntology(ont.ontology);
      setGraph(g);
      setVersion(v => v + 1);
      setError(null);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, []);

  useEffect(() => { void loadDomains(); }, [loadDomains]);

  useEffect(() => {
    const onHash = () => setActive(readHash());
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);

  // Unknown or missing domain → first domain (or the welcome screen).
  useEffect(() => {
    if (!domains) return;
    if (!active || !domains.some(d => d.id === active)) {
      const next = domains[0]?.id ?? null;
      if (next !== active) select(next);
    }
  }, [domains, active]);

  useEffect(() => {
    setDetail(null);
    setOntology(null);
    setGraph(EMPTY_GRAPH);
    setHighlight(new Set());
    setSelected(null);
    if (active) void loadDomain(active);
  }, [active, loadDomain]);

  // The ontology is generated in the background after creation (minutes): poll until it lands.
  useEffect(() => {
    if (!active || !detail || detail.ontology_generated) return;
    const t = setInterval(async () => {
      const det = await kg.domain(active).catch(() => null);
      if (det?.ontology_generated) {
        await loadDomain(active);
        await loadDomains();
      }
    }, 5000);
    return () => clearInterval(t);
  }, [active, detail, loadDomain, loadDomains]);

  // Agents add knowledge in the background: reload when the counts change.
  useEffect(() => {
    if (!active || !detail) return;
    const t = setInterval(async () => {
      const det = await kg.domain(active).catch(() => null);
      const s = det?.stats, old = detail.stats;
      if (s && (s.entity_count !== old.entity_count || s.fact_count !== old.fact_count
        || s.invalid_fact_count !== old.invalid_fact_count || s.pending_reviews !== old.pending_reviews)) {
        await loadDomain(active);
      }
    }, 15000);
    return () => clearInterval(t);
  }, [active, detail, loadDomain]);

  function select(id: string | null) {
    history.replaceState(null, '', id ? `#/${encodeURIComponent(id)}` : '#/');
    setActive(id);
  }

  async function createDomain(name: string, description: string) {
    const id = await kg.createDomain(name, description);
    await loadDomains();
    select(id);
    // Start the research bootstrap right away when the research service is up; otherwise ingest by hand.
    try {
      await research.start(id, 'bootstrap');
      setTab('research');
    } catch {
      setTab('ingest');
    }
  }

  async function deleteDomain(id: string) {
    await kg.deleteDomain(id);
    localStorage.removeItem(`sovereign.chat.${id}`);
    await loadDomains();
  }

  const refresh = useCallback(async () => {
    if (active) await loadDomain(active);
  }, [active, loadDomain]);

  const entityTypes = ontology?.entity_types ?? [];

  return (
    <div className="flex h-full">
      <Sidebar domains={domains ?? []} active={active} onSelect={select} onCreate={createDomain} />

      <main className="flex min-w-0 flex-1 flex-col">
        {error && (
          <div className="border-b border-bad/30 bg-bad/10 px-5 py-2 text-xs text-bad">{error}</div>
        )}

        {domains && domains.length === 0 && <Welcome onCreate={createDomain} />}

        {active && detail && (
          <>
            <DomainHeader detail={detail} onDelete={() => deleteDomain(active)} />

            <div className="flex min-h-0 flex-1 max-lg:flex-col max-lg:overflow-y-auto">
              <section className="relative min-w-0 border-line lg:flex-1 lg:border-r max-lg:h-[48vh] max-lg:min-h-[300px] max-lg:shrink-0 max-lg:border-b">
                <GraphView
                  graph={graph}
                  entityTypes={entityTypes}
                  highlight={highlight}
                  selected={selected}
                  onSelect={setSelected}
                />
                {graph.entities.length === 0 && (
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-center">
                    <Network className="text-faint" size={32} />
                    <p className="text-sm text-dim">This knowledge graph is empty.</p>
                    <p className="text-xs text-faint">Ingest text, or tell the Head Agent something worth keeping.</p>
                  </div>
                )}
                {highlight.size > 0 && (
                  <button
                    onClick={() => setHighlight(new Set())}
                    className="absolute top-3 left-3 rounded border border-line-2 bg-panel px-2.5 py-1 text-[11px] text-dim hover:text-ink"
                  >
                    Clear highlight ({highlight.size})
                  </button>
                )}
                {selected && (
                  <EntityPanel
                    domain={active}
                    uid={selected}
                    entityTypes={entityTypes}
                    version={version}
                    onClose={() => setSelected(null)}
                    onSelect={setSelected}
                  />
                )}
              </section>

              <aside className="flex w-full flex-col lg:w-[440px] lg:shrink-0 max-lg:min-h-[480px] max-lg:flex-1">
                <nav className="flex border-b border-line">
                  {TABS.map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      onClick={() => setTab(id)}
                      className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-2.5 text-xs transition-colors ${
                        tab === id ? 'border-gold text-ink' : 'border-transparent text-faint hover:text-dim'
                      }`}
                    >
                      <Icon size={13} /> {label}
                      {id === 'review' && detail.stats.pending_reviews > 0 && (
                        <span className="rounded-full bg-gold px-1.5 font-mono text-[9.5px] text-black">{detail.stats.pending_reviews}</span>
                      )}
                    </button>
                  ))}
                </nav>
                <div className="min-h-0 flex-1">
                  {/* Panels stay mounted so a running chat or ingest survives tab switches. */}
                  <div className={tab === 'ask' ? 'h-full' : 'hidden'}>
                    <AskPanel key={active} domain={active} onKnowledgeChanged={refresh} onHighlight={setHighlight} />
                  </div>
                  <div className={tab === 'research' ? 'h-full' : 'hidden'}>
                    <ResearchPanel key={active} domain={active} onIngesting={refresh} />
                  </div>
                  <div className={tab === 'ingest' ? 'h-full' : 'hidden'}>
                    <IngestPanel
                      key={active}
                      domain={active}
                      onIngested={async uids => { await refresh(); setHighlight(new Set(uids)); }}
                      ontologyReady={detail.ontology_generated}
                    />
                  </div>
                  <div className={tab === 'search' ? 'h-full' : 'hidden'}>
                    <SearchPanel
                      key={active}
                      domain={active}
                      entityTypes={entityTypes}
                      onHighlight={setHighlight}
                      onSelect={setSelected}
                    />
                  </div>
                  <div className={tab === 'review' ? 'h-full' : 'hidden'}>
                    <ReviewPanel key={active} domain={active} version={version} onChanged={refresh} />
                  </div>
                  <div className={tab === 'ontology' ? 'h-full' : 'hidden'}>
                    {ontology && (
                      <OntologyPanel
                        key={active}
                        domain={active}
                        ontology={ontology}
                        generated={detail.ontology_generated}
                        counts={detail.stats.categories}
                        onChanged={async () => { await refresh(); await loadDomains(); }}
                      />
                    )}
                  </div>
                </div>
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
