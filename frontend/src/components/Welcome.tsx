import { DomainForm } from './DomainForm';

export function Welcome({ onCreate }: { onCreate: (name: string, description: string) => Promise<void> }) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-md">
        <div className="mb-1 font-mono text-[11px] tracking-[0.3em] text-gold">THE SOVEREIGN</div>
        <h1 className="mb-2 text-xl font-medium">Build a domain expert.</h1>
        <p className="mb-6 text-sm leading-relaxed text-dim">
          Each domain gets its own knowledge graph. Feed it text, and the pipeline extracts, validates and links
          facts into it. The Head Agent answers from what the graph actually knows.
        </p>
        <div className="rounded-lg border border-line bg-panel p-5">
          <DomainForm onCreate={onCreate} />
        </div>
      </div>
    </div>
  );
}
