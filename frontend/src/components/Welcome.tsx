import { DomainForm } from './DomainForm';

export function Welcome({ onCreate }: { onCreate: (name: string, description: string) => Promise<void> }) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-md">
        <div className="mb-1 font-mono text-[11px] tracking-[0.3em] text-accent">THE SOVEREIGN</div>
        <h1 className="mb-2 text-xl font-medium">Start a research lab that never stops.</h1>
        <p className="mb-6 text-sm leading-relaxed text-dim">
          Give it a topic. The Head asks what you really want to know and writes a research charter with you, then
          keeps researching — every fact checked against its source and kept in a knowledge graph you can question.
        </p>
        <div className="rounded-lg border border-line bg-panel p-5">
          <DomainForm onCreate={onCreate} />
        </div>
      </div>
    </div>
  );
}
