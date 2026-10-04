import { useEffect, useRef, useState } from 'react';
import { Check, Loader2, Mic, Send, Square, Trash2, Volume2, VolumeX, Wrench } from 'lucide-react';
import { hermes, type AgentEvent, type Turn } from '../lib/api';
import { Markdown } from '../components/Markdown';
import { canListen, canSpeak, listen, speak, stopSpeaking } from '../lib/voice';

interface Step {
  id: string;
  name: string;
  args: unknown;
  done: boolean;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  steps?: Step[];
  error?: boolean;
}

function loadChat(domain: string): Message[] {
  try { return JSON.parse(localStorage.getItem(`sovereign.chat.${domain}`) ?? '[]'); } catch { return []; }
}

function argSummary(args: unknown): string {
  if (!args || typeof args !== 'object') return String(args ?? '');
  const a = args as Record<string, unknown>;
  const main = a.query ?? a.raw_text ?? '';
  return typeof main === 'string' ? main.slice(0, 90) : '';
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <div className="mb-2 space-y-1">
      {steps.map(s => (
        <div key={s.id} className="flex items-center gap-1.5 text-[11px] text-faint">
          {s.done ? <Check size={11} className="text-good" /> : <Loader2 size={11} className="animate-spin text-gold" />}
          <Wrench size={10} />
          <span className="font-mono text-dim">{s.name}</span>
          <span className="truncate">{argSummary(s.args)}</span>
        </div>
      ))}
    </div>
  );
}

export function AskPanel({ domain, onKnowledgeChanged, onHighlight }: {
  domain: string;
  onKnowledgeChanged: () => Promise<void>;
  onHighlight: (uids: Set<string>) => void;
}) {
  const [messages, setMessages] = useState<Message[]>(() => loadChat(domain));
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [steps, setSteps] = useState<Step[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [listening, setListening] = useState(false);
  const [voiceOut, setVoiceOut] = useState(() => localStorage.getItem('sovereign.voiceOut') === '1');
  const abort = useRef<AbortController | null>(null);
  const stopListening = useRef<() => void>(() => {});
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(`sovereign.chat.${domain}`, JSON.stringify(messages));
  }, [domain, messages]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [messages, draft, steps]);

  useEffect(() => {
    if (!busy) return;
    const start = Date.now();
    setElapsed(0);
    const t = setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);

  useEffect(() => () => { abort.current?.abort(); stopListening.current(); stopSpeaking(); }, []);

  async function send(text: string) {
    text = text.trim();
    if (!text || busy) return;
    const history: Turn[] = messages.filter(m => !m.error).map(m => ({ role: m.role, content: m.content }));
    setMessages(m => [...m, { role: 'user', content: text }]);
    setInput('');
    setBusy(true);
    setDraft('');
    setSteps([]);
    const controller = new AbortController();
    abort.current = controller;
    const collected: Step[] = [];
    let final: Message | null = null;

    const onEvent = (e: AgentEvent) => {
      switch (e.type) {
        case 'tool_start':
          collected.push({ id: e.id, name: e.name, args: e.args, done: false });
          setSteps([...collected]);
          break;
        case 'tool_end': {
          const s = collected.find(x => x.id === e.id);
          if (s) s.done = true;
          setSteps([...collected]);
          // Light up what the agent read (query) or stored (ingest) in the graph.
          if (e.name === 'ingest_data' || e.name === 'update_ontology') {
            void onKnowledgeChanged().then(() => { if (e.uids.length) onHighlight(new Set(e.uids)); });
          } else if (e.uids.length) {
            onHighlight(new Set(e.uids));
          }
          break;
        }
        case 'delta':
          setDraft(d => d + e.text);
          break;
        case 'answer':
          final = { role: 'assistant', content: e.text || '(no answer)', steps: collected };
          break;
        case 'error':
          final = { role: 'assistant', content: e.detail, steps: collected, error: true };
          break;
      }
    };

    try {
      await hermes.askStream(domain, text, history, onEvent, controller.signal);
    } catch (e) {
      final = controller.signal.aborted
        ? { role: 'assistant', content: 'Stopped.', steps: collected, error: true }
        : { role: 'assistant', content: (e as Error).message, steps: collected, error: true };
    }
    const answer: Message = final ?? { role: 'assistant', content: 'The Head Agent ended without an answer.', steps: collected, error: true };
    setMessages(m => [...m, answer]);
    setBusy(false);
    setDraft('');
    setSteps([]);
    abort.current = null;
    if (voiceOut && !answer.error) speak(answer.content);
  }

  function toggleMic() {
    if (listening) { stopListening.current(); return; }
    setListening(true);
    stopListening.current = listen(setInput, text => {
      setListening(false);
      if (text) void send(text);
    });
  }

  function toggleVoiceOut() {
    const next = !voiceOut;
    setVoiceOut(next);
    localStorage.setItem('sovereign.voiceOut', next ? '1' : '0');
    if (!next) stopSpeaking();
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <span className="text-[11px] text-faint">Answers grounded in this domain's knowledge graph</span>
        <div className="flex gap-1">
          {canSpeak && (
            <button onClick={toggleVoiceOut} className={`rounded p-1 ${voiceOut ? 'text-gold' : 'text-faint hover:text-ink'}`} title="Read answers aloud">
              {voiceOut ? <Volume2 size={13} /> : <VolumeX size={13} />}
            </button>
          )}
          <button
            onClick={() => setMessages([])}
            disabled={busy || messages.length === 0}
            className="rounded p-1 text-faint hover:text-ink disabled:opacity-30"
            title="Clear conversation"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !busy && (
          <div className="mt-10 space-y-2 text-center text-xs text-faint">
            <p className="font-mono tracking-widest text-dim">HEAD AGENT</p>
            <p>"What do we know about …?"</p>
            <p>"Ingest this: …"</p>
            <p>"What's the biggest risk right now?"</p>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="ml-8 rounded-lg bg-panel-2 px-3 py-2 text-[13px] whitespace-pre-wrap">{m.content}</div>
          ) : (
            <div key={i} className={`border-l-2 pl-3 ${m.error ? 'border-bad/60' : 'border-gold/60'}`}>
              {m.steps && m.steps.length > 0 && <Steps steps={m.steps} />}
              {m.error ? <p className="text-[13px] text-bad">{m.content}</p> : <Markdown>{m.content}</Markdown>}
            </div>
          ),
        )}

        {busy && (
          <div className="border-l-2 border-gold/60 pl-3">
            {steps.length > 0 && <Steps steps={steps} />}
            {draft ? (
              <Markdown>{draft}</Markdown>
            ) : (
              <p className="flex items-center gap-2 text-xs text-faint">
                <Loader2 size={12} className="animate-spin" /> thinking… {elapsed}s
                {elapsed > 30 && <span>(longer with thinking on or many tool calls)</span>}
              </p>
            )}
          </div>
        )}
      </div>

      <form
        onSubmit={e => { e.preventDefault(); void send(input); }}
        className="flex items-end gap-2 border-t border-line p-3"
      >
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input); } }}
          rows={2}
          placeholder={listening ? 'Listening…' : 'Ask the Head Agent…  (Enter to send)'}
          className="min-h-[44px] flex-1 resize-none rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] outline-none focus:border-gold/60"
        />
        {canListen && (
          <button
            type="button"
            onClick={toggleMic}
            disabled={busy}
            className={`rounded-md border p-2.5 ${listening ? 'border-bad text-bad pulse-dot' : 'border-line-2 text-dim hover:text-ink'} disabled:opacity-30`}
            title="Speak"
          >
            <Mic size={15} />
          </button>
        )}
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()} className="rounded-md border border-line-2 p-2.5 text-dim hover:text-bad" title="Stop">
            <Square size={15} />
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()} className="rounded-md bg-gold p-2.5 text-black disabled:opacity-30" title="Send">
            <Send size={15} />
          </button>
        )}
      </form>
    </div>
  );
}
