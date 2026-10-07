import { useEffect, useState } from 'react';
import { CircleUser, Plus } from 'lucide-react';
import { hermes, kg, LANGFUSE_URL, research, type DomainSummary, type User } from '../lib/api';
import { AccountDialog } from './AccountDialog';
import { NewDomainDialog } from './NewDomainDialog';

interface Health {
  kg: boolean | null;
  arcadedb: boolean | null;
  hermes: boolean | null;
  research: boolean | null;
  search: boolean | null;
  crawler: boolean | null;
  tracing: boolean | null;
  voice: string | null;
}

function useHealth(): Health {
  const [health, setHealth] = useState<Health>({ kg: null, arcadedb: null, hermes: null, research: null, search: null, crawler: null, tracing: null, voice: null });
  useEffect(() => {
    let alive = true;
    const check = async () => {
      const [k, h, r] = await Promise.allSettled([kg.health(), hermes.health(), research.health()]);
      if (!alive) return;
      setHealth({
        kg: k.status === 'fulfilled',
        arcadedb: k.status === 'fulfilled' ? k.value.arcadedb : null,
        hermes: h.status === 'fulfilled',
        research: r.status === 'fulfilled',
        search: r.status === 'fulfilled' ? r.value.searxng : null,
        crawler: r.status === 'fulfilled' ? r.value.crawler : null,
        tracing: k.status === 'fulfilled' ? k.value.tracing : null,
        voice: h.status === 'fulfilled' && h.value.voice ? `${h.value.voice.stt} · ${h.value.voice.tts}` : null,
      });
    };
    void check();
    const t = setInterval(check, 10000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return health;
}

function Status({ label, ok, hint }: { label: string; ok: boolean | null; hint: string }) {
  const color = ok === null ? 'bg-faint' : ok ? 'bg-good' : 'bg-bad';
  return (
    <div className="flex items-center justify-between text-[11px]" title={hint}>
      <span className="text-faint">{label}</span>
      <span className={`h-1.5 w-1.5 rounded-full ${color}`} />
    </div>
  );
}

export function Sidebar({ domains, active, onSelect, onCreate, me, onSignOut }: {
  domains: DomainSummary[];
  active: string | null;
  onSelect: (id: string) => void;
  onCreate: (name: string, description: string) => Promise<void>;
  me: User;
  onSignOut: () => Promise<void>;
}) {
  const [creating, setCreating] = useState(false);
  const [account, setAccount] = useState(false);
  const health = useHealth();

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-panel max-md:w-44">
      <div className="px-4 pt-5 pb-4">
        <div className="font-mono text-[13px] font-semibold tracking-[0.25em] text-gold">SOVEREIGN</div>
        <div className="mt-0.5 text-[10.5px] text-faint">domain knowledge graphs</div>
      </div>

      <div className="flex items-center justify-between px-4 pb-2">
        <span className="font-mono text-[10px] tracking-widest text-faint uppercase">Domains</span>
        <button
          onClick={() => setCreating(true)}
          className="rounded p-1 text-dim hover:bg-panel-2 hover:text-ink"
          title="New domain"
        >
          <Plus size={14} />
        </button>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2">
        {domains.map(d => (
          <button
            key={d.id}
            onClick={() => onSelect(d.id)}
            className={`mb-0.5 w-full rounded-md px-2.5 py-2 text-left transition-colors ${
              d.id === active ? 'bg-panel-2 text-ink' : 'text-dim hover:bg-panel-2/60 hover:text-ink'
            }`}
          >
            <div className="flex items-center gap-2">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${d.ontology_generated ? 'bg-gold' : 'bg-faint pulse-dot'}`} />
              <span className="truncate font-mono text-[12px]">{d.id}</span>
              {d.role !== 'owner' && <span className="ml-auto shrink-0 text-[9.5px] text-faint">{d.role}</span>}
            </div>
            {d.description && (
              <div className="mt-0.5 truncate pl-3.5 text-[10.5px] text-faint">{d.description}</div>
            )}
          </button>
        ))}
        {domains.length === 0 && <p className="px-2.5 py-2 text-[11px] text-faint">No domains yet.</p>}
      </nav>

      <div className="space-y-1.5 border-t border-line px-4 py-3">
        <Status label="KG API" ok={health.kg} hint="kg/ — http://localhost:8080" />
        <Status label="ArcadeDB" ok={health.arcadedb} hint="docker compose up -d — http://localhost:2480" />
        <Status label="Hermes" ok={health.hermes} hint="hermes/ — http://localhost:8090" />
        <Status label="Voice (local)" ok={health.hermes === null ? null : !!health.voice} hint={health.voice ?? 'speech runs in Hermes'} />
        <Status label="Research" ok={health.research} hint="research/ — http://localhost:8070" />
        <Status label="Search · Crawler" ok={health.search === null ? null : !!(health.search && health.crawler)} hint="docker compose up -d (searxng, crawl4ai)" />
        {health.tracing && me.admin && (
          <a href={LANGFUSE_URL} target="_blank" rel="noreferrer" className="block text-[11px] text-faint hover:text-ink" title="Agent traces — docker compose --profile observability up -d">
            LangFuse traces ↗
          </a>
        )}
      </div>

      <button
        onClick={() => setAccount(true)}
        className="flex items-center gap-2 border-t border-line px-4 py-2.5 text-left text-dim hover:bg-panel-2 hover:text-ink"
        title="Account: password, API tokens, invites, sign out"
      >
        <CircleUser size={14} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate text-[11.5px]">{me.name || me.email}</span>
        {me.admin && <span className="text-[9.5px] text-gold">admin</span>}
      </button>

      {creating && <NewDomainDialog onCreate={onCreate} onClose={() => setCreating(false)} />}
      {account && <AccountDialog user={me} onClose={() => setAccount(false)} onSignOut={onSignOut} />}
    </aside>
  );
}
