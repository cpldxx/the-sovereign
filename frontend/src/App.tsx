import { useCallback, useEffect, useState } from 'react';
import { Bot, Network, Newspaper, Settings2, Workflow } from 'lucide-react';
import { ApiError, auth, kg, onSignedOut, research, type DomainDetail, type DomainSummary, type Ontology, type User } from './lib/api';
import { Sidebar } from './components/Sidebar';
import { SignIn } from './components/SignIn';
import { DomainHeader } from './components/DomainHeader';
import { AgentView } from './components/AgentView';
import { GraphPane } from './components/GraphPane';
import { EntityPanel } from './components/EntityPanel';
import { Welcome } from './components/Welcome';
import { AskPanel } from './panels/AskPanel';
import { PendingActions } from './panels/ActionsPanel';
import { BriefingPanel, type BriefingView } from './panels/BriefingPanel';
import { AlertToasts } from './components/AlertToasts';
import { SettingsPanel } from './panels/SettingsPanel';

// Three tabs: you talk to the Head (and confirm what it wants to do), read what happened, and set things up. The rest
// is the Head's work, seen on the left — click an agent for its conversations; search lives in the graph tab.
type Tab = 'head' | 'briefing' | 'settings';

const TABS: { id: Tab; label: string; icon: typeof Bot }[] = [
  { id: 'head', label: 'Head', icon: Bot },
  { id: 'briefing', label: 'Briefing', icon: Newspaper },
  { id: 'settings', label: 'Settings', icon: Settings2 },
];


/** Active domain lives in the URL hash (#/quant_trading) so links and reloads keep it. */
function readHash(): string | null {
  const id = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  return id && !id.startsWith('invite/') ? id : null;
}

/** An invite link: #/invite/<code>. */
function readInvite(): string | null {
  const m = location.hash.match(/^#\/invite\/([\w-]+)/);
  return m ? m[1] : null;
}

/** What this browser kept for the last account (chats, greetings) — not for the next one to see. */
function clearLocal() {
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith('sovereign.chat.') || key.startsWith('sovereign.greeted')) localStorage.removeItem(key);
  }
}

/** Signed in → the workspace; otherwise the sign-in page. */
export default function App() {
  const [me, setMe] = useState<User | null | undefined>(undefined);
  const [invite, setInvite] = useState(readInvite);

  useEffect(() => {
    auth.me().then(r => setMe(r.user)).catch(e => setMe(e instanceof ApiError && e.status === 401 ? null : undefined));
    const onHash = () => setInvite(readInvite());
    addEventListener('hashchange', onHash);
    const off = onSignedOut(() => { clearLocal(); setMe(null); });
    return () => { off(); removeEventListener('hashchange', onHash); };
  }, []);

  if (me === undefined) return <div className="flex h-full items-center justify-center text-xs text-faint">Connecting…</div>;
  if (me === null) return <SignIn key={invite ?? ''} invite={invite} onSignedIn={setMe} />;
  return (
    <Workspace
      key={me.uid}
      me={me}
      onSignOut={async () => {
        await auth.logout().catch(() => undefined);
        clearLocal();
        setMe(null);
      }}
    />
  );
}

function Workspace({ me, onSignOut }: { me: User; onSignOut: () => Promise<void> }) {
  const [domains, setDomains] = useState<DomainSummary[] | null>(null);
  const [active, setActive] = useState<string | null>(readHash);
  const [detail, setDetail] = useState<DomainDetail | null>(null);
  const [ontology, setOntology] = useState<Ontology | null>(null);
  // The main view: the agents at work (default) or the knowledge graph, which only exists while it is open.
  const [view, setView] = useState<'agents' | 'graph'>('agents');
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('head');
  const [briefing, setBriefing] = useState<BriefingView>('report');
  const openDone = useCallback(() => { setTab('briefing'); setBriefing('activity'); }, []);
  const [version, setVersion] = useState(0);  // bumps on every reload, so open panels refetch
  const [pendingActions, setPendingActions] = useState(0);
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
      const [det, ont] = await Promise.all([kg.domain(id), kg.ontology(id)]);
      setDetail(det);
      setOntology(ont.ontology);
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
    // Start the research bootstrap right away when the research service is up (the Researcher lights up on the
    // left); otherwise paste sources into the Head chat.
    setView('agents');
    setTab('head');
    await research.start(id, 'bootstrap').catch(() => undefined);
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
      <Sidebar domains={domains ?? []} active={active} onSelect={select} onCreate={createDomain} me={me} onSignOut={onSignOut} />

      <main className="flex min-w-0 flex-1 flex-col">
        {error && (
          <div className="border-b border-bad/30 bg-bad/10 px-5 py-2 text-xs text-bad">{error}</div>
        )}

        {domains && domains.length === 0 && <Welcome onCreate={createDomain} />}

        {active && detail && (
          <>
            <DomainHeader detail={detail} me={me} onDelete={() => deleteDomain(active)} onLeft={loadDomains} />

            <div className="flex min-h-0 flex-1 max-lg:flex-col max-lg:overflow-y-auto">
              <section className="relative flex min-w-0 flex-col border-line lg:flex-1 lg:border-r max-lg:h-[56vh] max-lg:min-h-[320px] max-lg:shrink-0 max-lg:border-b">
                <nav className="flex items-center gap-5 border-b border-line px-4">
                  {([['agents', 'Agents', Workflow], ['graph', `Knowledge graph · ${detail.stats.entity_count}`, Network]] as const).map(([id, label, Icon]) => (
                    <button key={id} onClick={() => setView(id)}
                      className={`-mb-px flex items-center gap-1.5 border-b py-2.5 font-mono text-[10.5px] tracking-[0.14em] uppercase transition-colors ${
                        view === id ? 'border-accent text-accent' : 'border-transparent text-faint hover:text-dim'}`}>
                      <Icon size={12} /> {label}
                    </button>
                  ))}
                  {view === 'agents' && highlight.size > 0 && (
                    <button onClick={() => setView('graph')} className="ml-auto rounded border border-accent/40 px-2 py-0.5 text-[11px] text-accent hover:bg-accent/10">
                      See the {highlight.size} entities the Head used in the graph →
                    </button>
                  )}
                </nav>
                <div className="relative min-h-0 flex-1">
                  {view === 'agents'
                    ? <AgentView key={active} domain={active} onIngesting={refresh}
                        stats={{ entities: detail.stats.entity_count, facts: detail.stats.fact_count }} />
                    : <GraphPane key={active} domain={active} entityTypes={entityTypes} highlight={highlight} selected={selected}
                        version={version} onSelect={setSelected} onHighlight={setHighlight}
                        onClearHighlight={() => setHighlight(new Set())} />}
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
                </div>
              </section>

              <aside className="flex w-full flex-col lg:w-[440px] lg:shrink-0 max-lg:min-h-[480px] max-lg:flex-1">
                <nav className="flex border-b border-line">
                  {TABS.map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      onClick={() => setTab(id)}
                      className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-2.5 text-xs transition-colors ${
                        tab === id ? 'border-accent text-ink' : 'border-transparent text-faint hover:text-dim'
                      }`}
                    >
                      <Icon size={13} className="shrink-0" /> {label}
                      {id === 'head' && pendingActions > 0 && (
                        <span className="rounded-full bg-accent px-1.5 font-mono text-[9.5px] text-black" title="Waiting for your confirmation">{pendingActions}</span>
                      )}
                    </button>
                  ))}
                </nav>
                <div className="min-h-0 flex-1">
                  {/* Panels stay mounted so a running chat survives tab switches. */}
                  <div className={tab === 'head' ? 'flex h-full flex-col' : 'hidden'}>
                    <PendingActions key={active} domain={active} version={version} onSelect={setSelected} onPending={setPendingActions} />
                    <div className="min-h-0 flex-1">
                      <AskPanel key={active} domain={active} onKnowledgeChanged={refresh} onHighlight={setHighlight}
                        ontologyReady={detail.ontology_generated} onOpenBriefing={openDone} />
                    </div>
                  </div>
                  <div className={tab === 'briefing' ? 'h-full' : 'hidden'}>
                    <BriefingPanel key={active} domain={active} version={version} view={briefing} onView={setBriefing}
                      onHighlight={setHighlight}
                      onSelect={setSelected} onPending={setPendingActions} />
                  </div>
                  <div className={tab === 'settings' ? 'h-full' : 'hidden'}>
                    <SettingsPanel key={active} domain={active} detail={detail} ontology={ontology} version={version}
                      onSelect={setSelected} onHighlight={setHighlight} onPending={setPendingActions} onChanged={refresh}
                      onOntologyChanged={async () => { await refresh(); await loadDomains(); }} />
                  </div>
                </div>
              </aside>
              <AlertToasts key={active} domain={active} onOpen={openDone} />
            </div>
          </>
        )}
      </main>
    </div>
  );
}
