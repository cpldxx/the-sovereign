import { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useParams, Navigate } from 'react-router';
import { KnowledgeGraph } from './components/KnowledgeGraph';
import { IngestPanel } from './components/IngestPanel';
import { ChatPanel } from './components/ChatPanel';
import { LandingPage } from './components/LandingPage';
import '../styles/graph.css';

import { KG_API, createDomain } from './api';

/* ===== Domain storage ===== */
const STORAGE_KEY = 'sovereign_domains';
function loadDomains(): string[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]'); }
  catch { return []; }
}
function saveDomains(d: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(d));
}

/* ===== Landing ===== */
function Landing() {
  const navigate = useNavigate();

  const handleEnter = async (name: string) => {
    let domain: string;
    try {
      domain = await createDomain(name);
    } catch (e) {
      // Already exists → enter it; anything else → tell the user.
      const msg = e instanceof Error ? e.message : String(e);
      const existing = msg.match(/Domain '([a-z0-9_]+)' already exists/);
      if (!existing) { alert(`Could not create domain: ${msg}`); return; }
      domain = existing[1];
    }
    const known = loadDomains();
    saveDomains(known.includes(domain) ? known : [domain, ...known]);
    navigate(`/workspace/${encodeURIComponent(domain)}`);
  };

  return <LandingPage onEnter={handleEnter} />;
}

type WorkspaceTab = 'graph' | 'ingest' | 'ask';

const TABS: { id: WorkspaceTab; label: string }[] = [
  { id: 'graph', label: 'Graph' },
  { id: 'ingest', label: 'Ingest' },
  { id: 'ask', label: 'Ask' },
];

/* ===== Delete confirm modal ===== */
function DeleteModal({ domain, onCancel, onConfirm }: {
  domain: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}
      onClick={onCancel}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--surface)', border: '1px solid var(--border-2)',
          borderRadius: 4, padding: '28px 32px', width: 420,
          fontFamily: 'var(--mono)',
        }}
      >
        <div style={{ fontSize: 10, letterSpacing: '0.2em', color: 'var(--warn)', textTransform: 'uppercase', marginBottom: 14 }}>
          Delete domain
        </div>
        <div style={{ fontSize: 15, fontFamily: 'var(--sans)', color: 'var(--text)', marginBottom: 8, lineHeight: 1.4 }}>
          Delete <span style={{ color: 'var(--accent)' }}>{domain}</span>?
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text-dim)', marginBottom: 28, lineHeight: 1.6, fontFamily: 'var(--sans)' }}>
          All nodes, edges, and the knowledge graph for this domain will be permanently removed. This cannot be undone.
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            style={{
              padding: '7px 18px', background: 'transparent',
              border: '1px solid var(--border-2)', borderRadius: 2, cursor: 'pointer',
              fontFamily: 'var(--mono)', fontSize: 10.5, letterSpacing: '0.1em',
              color: 'var(--text-dim)', textTransform: 'uppercase', transition: 'all 150ms',
            }}
            onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--border-3)')}
            onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-2)')}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            style={{
              padding: '7px 18px', background: 'rgba(252,129,129,0.12)',
              border: '1px solid var(--warn)', borderRadius: 2, cursor: 'pointer',
              fontFamily: 'var(--mono)', fontSize: 10.5, letterSpacing: '0.1em',
              color: 'var(--warn)', textTransform: 'uppercase', transition: 'all 150ms',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(252,129,129,0.22)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(252,129,129,0.12)'; }}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

/* ===== Workspace ===== */
function Workspace() {
  const navigate = useNavigate();
  const { domain: domainParam } = useParams<{ domain: string }>();
  const [domains, setDomains] = useState<string[]>(() => loadDomains());
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('graph');
  const [graphVersion, setGraphVersion] = useState(0);
  const [hoveredDomain, setHoveredDomain] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  // Domains come from the API; until that answers, localStorage is only a guess.
  const [synced, setSynced] = useState(false);

  const handleIngestSuccess = () => {
    setGraphVersion(v => v + 1);
    setActiveTab('graph');
  };

  const activeDomain = domainParam ? decodeURIComponent(domainParam) : '';

  // No domains at all → back to landing (only once the API has been asked)
  useEffect(() => {
    if (synced && domains.length === 0) navigate('/');
  }, [synced, domains, navigate]);

  // Sync domains from API
  useEffect(() => {
    let cancelled = false;  // set on unmount; a timeout abort still counts as synced
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    fetch(`${KG_API}/domains`, { signal: controller.signal })
      .then(r => r.json())
      .then(d => {
        const api: string[] = d.domains ?? [];
        setDomains(api);
        saveDomains(api);
      })
      .catch(() => {})  // API down → keep the localStorage list
      .finally(() => {
        clearTimeout(timeout);
        if (!cancelled) setSynced(true);
      });
    return () => { cancelled = true; controller.abort(); };
  }, []);

  const handleDeleteDomain = async (name: string) => {
    try {
      await fetch(`${KG_API}/domains/${encodeURIComponent(name)}`, { method: 'DELETE' });
    } catch {}
    const updated = domains.filter(d => d !== name);
    setDomains(updated);
    saveDomains(updated);
    setDeleteTarget(null);
    if (activeDomain === name) {
      navigate(updated.length > 0 ? `/workspace/${encodeURIComponent(updated[0])}` : '/');
    }
  };

  const handleNewDomain = async () => {
    const name = prompt('Domain name:');
    if (!name?.trim()) return;
    let domain: string;
    try {
      domain = await createDomain(name.trim());
    } catch (e) {
      alert(`Could not create domain: ${e instanceof Error ? e.message : e}`);
      return;
    }
    const updated = domains.includes(domain) ? domains : [domain, ...domains];
    setDomains(updated);
    saveDomains(updated);
    navigate(`/workspace/${encodeURIComponent(domain)}`);
  };

  return (
    <>
    {deleteTarget && (
      <DeleteModal
        domain={deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => handleDeleteDomain(deleteTarget)}
      />
    )}
    <div style={{
      display: 'grid',
      gridTemplateRows: 'var(--topbar-h) 36px 1fr var(--statusbar-h)',
      height: '100%',
      background: 'var(--bg)',
    }}>

      {/* Topbar */}
      <header style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr auto',
        alignItems: 'center', gap: 24, padding: '0 18px',
        background: 'linear-gradient(to bottom, #0d1018 0%, #0a0c10 100%)',
        borderBottom: '1px solid var(--border)',
      }}>
        {/* Brand — click to go back to landing */}
        <button
          onClick={() => navigate('/')}
          style={{
            display: 'flex', alignItems: 'baseline', gap: 8,
            background: 'none', border: 'none', cursor: 'pointer',
            padding: 0, transition: 'opacity 150ms',
          }}
          onMouseEnter={e => (e.currentTarget.style.opacity = '0.7')}
          onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
        >
          <span style={{ color: 'var(--accent)', fontSize: 16, fontFamily: 'var(--mono)' }}>◆</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--text)', textTransform: 'uppercase' }}>
            Sovereign
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.04em', color: 'var(--text-faint)' }}>
            knowledge graph
          </span>
        </button>

        {/* Domain tabs */}
        <div style={{ display: 'flex', gap: 2, overflowX: 'auto' }}>
          {domains.map(d => (
            <div
              key={d}
              onMouseEnter={() => setHoveredDomain(d)}
              onMouseLeave={() => setHoveredDomain(null)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '0 6px 0 14px',
                border: activeDomain === d ? '1px solid var(--border-2)' : '1px solid transparent',
                borderBottom: 'none', borderRadius: '3px 3px 0 0',
                background: activeDomain === d ? 'var(--bg-2)' : 'transparent',
                transition: 'background 120ms',
              }}
            >
              <button
                onClick={() => navigate(`/workspace/${encodeURIComponent(d)}`)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer', padding: '8px 0',
                  fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.08em',
                  textTransform: 'uppercase', whiteSpace: 'nowrap',
                  color: activeDomain === d ? 'var(--accent)' : 'var(--text-dim)',
                  transition: 'color 120ms',
                }}
              >
                {d}
              </button>
              <button
                onClick={e => { e.stopPropagation(); setDeleteTarget(d); }}
                style={{
                  width: 16, height: 16, flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'none', border: 'none', cursor: 'pointer', borderRadius: 2,
                  color: 'var(--text-ghost)', fontSize: 11, lineHeight: 1,
                  opacity: hoveredDomain === d ? 1 : 0,
                  transition: 'opacity 120ms, color 120ms, background 120ms',
                }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--warn)'; e.currentTarget.style.background = 'rgba(252,129,129,0.12)'; }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-ghost)'; e.currentTarget.style.background = 'none'; }}
              >
                ✕
              </button>
            </div>
          ))}
          <button
            onClick={handleNewDomain}
            style={{
              padding: '8px 12px', fontFamily: 'var(--mono)', fontSize: 11,
              letterSpacing: '0.08em', color: 'var(--text-ghost)', cursor: 'pointer',
              border: '1px solid transparent', borderBottom: 'none',
              borderRadius: '3px 3px 0 0', background: 'transparent', transition: 'color 120ms',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-dim)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-ghost)')}
          >
            + NEW
          </button>
        </div>

        {/* Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--cyan)', boxShadow: '0 0 6px var(--cyan)', display: 'inline-block' }} />
          LIVE
        </div>
      </header>

      {/* Tab bar */}
      <div style={{
        display: 'flex', alignItems: 'stretch',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg)', paddingLeft: 18,
      }}>
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '0 16px',
              fontFamily: 'var(--mono)', fontSize: 10.5, letterSpacing: '0.12em',
              textTransform: 'uppercase', cursor: 'pointer',
              background: 'transparent', border: 'none',
              borderBottom: activeTab === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
              color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-faint)',
              transition: 'color 120ms, border-color 120ms',
              marginBottom: -1,
            }}
            onMouseEnter={e => { if (activeTab !== tab.id) e.currentTarget.style.color = 'var(--text-dim)'; }}
            onMouseLeave={e => { if (activeTab !== tab.id) e.currentTarget.style.color = 'var(--text-faint)'; }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Canvas */}
      <main style={{ position: 'relative', minHeight: 0, overflow: 'hidden', background: 'var(--bg-2)' }}>
        {activeTab === 'graph' && <KnowledgeGraph key={`${activeDomain}-${graphVersion}`} domain={activeDomain} />}
        {activeTab === 'ingest' && <IngestPanel domain={activeDomain} onSuccess={handleIngestSuccess} />}
        {activeTab === 'ask' && <ChatPanel key={activeDomain} domain={activeDomain} />}
      </main>

      {/* Statusbar */}
      <footer style={{
        display: 'flex', alignItems: 'center', gap: 18, padding: '0 16px',
        background: '#060709', borderTop: '1px solid var(--border)',
        fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--text-dim)', letterSpacing: '0.04em',
      }}>
        <span style={{ color: 'var(--text-faint)' }}>DOMAIN</span>
        <span style={{ color: 'var(--accent)' }}>{activeDomain}</span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}>
          {new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}
        </span>
      </footer>
    </div>
    </>
  );
}

/* ===== Router ===== */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/workspace/:domain" element={<Workspace />} />
      <Route path="/workspace" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
