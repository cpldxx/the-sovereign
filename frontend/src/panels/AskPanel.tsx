import { useEffect, useRef, useState } from 'react';
import { AudioLines, Bell, Check, Hourglass, Loader2, Mic, MicOff, Paperclip, Send, Square, Trash2, Volume2, VolumeX, Wrench, X } from 'lucide-react';
import { hermes, kg, type AgentEvent, type IngestResult, type Turn } from '../lib/api';
import { IngestPanel } from './IngestPanel';
import { Markdown } from '../components/Markdown';
import { canSpeak, speak, stopSpeaking } from '../lib/voice';
import { VoiceSession, type VoiceState } from '../lib/voiceSession';

/** "Sovereign, …" / "자비스, …" — in wake-word mode only addressed utterances (or follow-ups) are answered. */
const WAKE = /^\s*(?:hey\s+|헤이\s*)?(?:sovereign|sovren|jarvis|소[버벌브뻐]린|서버린|쏘버린|자비스)[\s,.!?:~]*/i;
const FOLLOW_UP_MS = 30_000;
const SPOKEN_SENTENCES = 3;  // read aloud; the whole answer stays on screen

const HANGUL = /[\uac00-\ud7a3]/;

/** The first sentences of an answer, for speaking it. */
function firstSentences(text: string, n: number): string {
  const plain = text.replace(/```[\s\S]*?```/g, ' ').replace(/^\s*\|.*\|\s*$/gm, ' ').replace(/\s+/g, ' ').trim();
  const parts = plain.split(/(?<=[.!?。])\s+/);
  return parts.length > n ? parts.slice(0, n).join(' ') : plain;
}

const VOICE_LABEL: Record<VoiceState, string> = {
  off: '', listening: 'Listening', hearing: 'Hearing you…', working: 'Thinking…', speaking: 'Speaking — talk to interrupt',
};

/** What the Head did on the way to its answer: a tool call, or what it said before one (kept, not replaced). */
interface Step {
  id: string;
  name: string;
  args: unknown;
  done: boolean;
  said?: string;
  outcome?: { action: string; status: string; title: string };   // an action the Operator ran or holds for you
}

/** The proposal a propose_action call returned, if the result is one. */
function actionOutcome(result: string): Step['outcome'] {
  try {
    const p = JSON.parse(result);
    if (!p?.action || !p?.status) return undefined;
    const t = p.params?.title ?? p.params?.question ?? p.params?.message ?? '';
    return { action: p.action, status: p.status, title: typeof t === 'string' ? t : '' };
  } catch {
    return undefined;
  }
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

function Outcome({ o, onOpen }: { o: NonNullable<Step['outcome']>; onOpen?: () => void }) {
  const waiting = o.status === 'proposed';
  const where = waiting ? 'waiting for your confirmation at the top of this tab'
    : o.action === 'alert' ? 'shown as a pop-up and in Briefing › What was done'
    : 'its result is in Briefing › What was done';
  return (
    <div className="rounded border border-line-2 bg-panel px-2.5 py-1.5 text-[11px]">
      <div className="flex items-center gap-1.5">
        {waiting ? <Hourglass size={11} className="shrink-0 text-accent" /> : <Bell size={11} className="shrink-0 text-accent" />}
        <span className="shrink-0 text-ink">{o.action === 'alert' && !waiting ? 'Alert sent' : `${o.action} ${o.status}`}</span>
        {o.title && <span className="min-w-0 truncate text-dim">“{o.title}”</span>}
      </div>
      <div className="mt-0.5 flex items-center gap-2 pl-[17px] text-faint">
        <span>{where}</span>
        {!waiting && onOpen && <button onClick={onOpen} className="ml-auto shrink-0 underline hover:text-ink">open</button>}
      </div>
    </div>
  );
}

function Steps({ steps, onOpenBriefing }: { steps: Step[]; onOpenBriefing?: () => void }) {
  return (
    <div className="mb-2 space-y-1">
      {steps.map(s => s.said ? (
        <p key={s.id} className="text-[12px] leading-snug text-dim">{s.said}</p>
      ) : s.outcome ? (
        <Outcome key={s.id} o={s.outcome} onOpen={onOpenBriefing} />
      ) : (
        <div key={s.id} className="flex items-center gap-1.5 text-[11px] text-faint">
          {s.done ? <Check size={11} className="text-good" /> : <Loader2 size={11} className="animate-spin text-accent" />}
          <Wrench size={10} />
          <span className="font-mono text-dim">{s.name}</span>
          <span className="truncate">{argSummary(s.args)}</span>
        </div>
      ))}
    </div>
  );
}

export function AskPanel({ domain, onKnowledgeChanged, onHighlight, ontologyReady, onOpenBriefing }: {
  domain: string;
  onKnowledgeChanged: () => Promise<void>;
  onHighlight: (uids: Set<string>) => void;
  ontologyReady: boolean;
  onOpenBriefing?: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>(() => loadChat(domain));
  const [attaching, setAttaching] = useState(false);   // a source pasted in: the pipeline learns it, the chat notes it
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [steps, setSteps] = useState<Step[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [voiceOut, setVoiceOut] = useState(() => localStorage.getItem('sovereign.voiceOut') === '1');
  const [voice, setVoice] = useState<VoiceState>('off');
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState('');
  const [wakeWord, setWakeWord] = useState(() => localStorage.getItem('sovereign.wakeWord') === '1');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const session = useRef<VoiceSession | null>(null);
  const lastAnswerAt = useRef(0);
  const heardRef = useRef('');
  const wakeRef = useRef(wakeWord);
  const messagesRef = useRef(messages);
  const scroller = useRef<HTMLDivElement>(null);
  wakeRef.current = wakeWord;
  messagesRef.current = messages;

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

  useEffect(() => () => { abort.current?.abort(); session.current?.stop(); stopSpeaking(); }, []);

  async function send(text: string, spoken = false, onFirstTool?: () => void): Promise<Message | null> {
    text = text.trim();
    if (!text || abort.current) return null;
    const history: Turn[] = messagesRef.current.filter(m => !m.error).map(m => ({ role: m.role, content: m.content }));
    setMessages(m => [...m, { role: 'user', content: text }]);
    setInput('');
    setBusy(true);
    setDraft('');
    setSteps([]);
    const controller = new AbortController();
    abort.current = controller;
    const collected: Step[] = [];
    let final: Message | null = null;
    let streamed = '';   // what the Head is saying now; kept as a step when it goes on to a tool

    const onEvent = (e: AgentEvent) => {
      switch (e.type) {
        case 'tool_start':
          if (!collected.length) onFirstTool?.();
          if (streamed.trim()) collected.push({ id: `said-${collected.length}`, name: '', args: null, done: true, said: streamed.trim() });
          streamed = '';
          setDraft('');
          collected.push({ id: e.id, name: e.name, args: e.args, done: false });
          setSteps([...collected]);
          break;
        case 'tool_end': {
          const s = collected.find(x => x.id === e.id);
          if (s) {
            s.done = true;
            if (e.name === 'propose_action') s.outcome = actionOutcome(e.result);
          }
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
          streamed += e.text;
          setDraft(streamed);
          break;
        case 'answer':
          final = { role: 'assistant', content: e.text || streamed.trim() || '(no answer)', steps: collected };
          break;
        case 'error':
          final = { role: 'assistant', content: e.detail, steps: collected, error: true };
          break;
      }
    };

    try {
      // Spoken questions get fewer tool rounds: an answer is waited for in silence.
      await hermes.askStream(domain, text, history, onEvent, controller.signal, spoken ? 6 : 8, spoken);
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
    if (voiceOut && !spoken && !answer.error) void readAloud(answer.content);
    return answer;
  }

  /** Local voice first (Hermes); the browser's own voice if Hermes can't speak. */
  async function readAloud(text: string) {
    try {
      const audio = new Audio(URL.createObjectURL(await hermes.speak(text)));
      await audio.play();
    } catch {
      speak(text);
    }
  }

  // ── Voice conversation ────────────────────────────────────────────────
  async function onUtterance(wav: Blob) {
    const s = session.current;
    if (!s) return;
    let { text } = await hermes.transcribe(wav).catch(e => { setVoiceError((e as Error).message); return { text: '' }; });
    setHeard(text);
    heardRef.current = text;
    if (!text) return;
    if (wakeRef.current && Date.now() - lastAnswerAt.current > FOLLOW_UP_MS) {
      if (!WAKE.test(text)) return;  // not addressed to Sovereign
    }
    text = text.replace(WAKE, '').trim();
    if (!text) { await say(HANGUL.test(heardRef.current) ? '네?' : 'Yes?'); return; }
    // The Head's tool work takes a while: acknowledge out loud as soon as it starts.
    const korean = HANGUL.test(text);
    const ack = hermes.speak(korean ? '확인해 볼게요.' : 'Let me check.').catch(() => null);
    const answer = await send(text, true, () => { void ack.then(blob => blob && s.cue(blob)); });
    if (answer && !answer.error) await say(firstSentences(answer.content, SPOKEN_SENTENCES));
  }

  async function say(text: string) {
    const s = session.current;
    if (!s) return;
    try {
      await s.play(await hermes.speak(text));
    } catch (e) {
      setVoiceError((e as Error).message);
    }
    lastAnswerAt.current = Date.now();
  }

  /** Once a day, starting voice opens with the day's briefing: the report's spoken summary and what waits for you. */
  async function greeting(): Promise<string | null> {
    const key = `sovereign.greeted.${domain}`;
    const today = new Date().toDateString();
    if (localStorage.getItem(key) === today) return null;
    localStorage.setItem(key, today);
    const [reports, pending] = await Promise.all([
      kg.reports(domain, 1).catch(() => null), kg.proposals(domain, 'proposed').catch(() => []),
    ]);
    const report = reports?.reports[0];
    const parts: string[] = [];
    if (report && Date.now() - new Date(report.created_at).getTime() < 36 * 3600_000) parts.push(report.spoken || report.headline);
    if (pending.length) parts.push(`${pending.length} action${pending.length > 1 ? 's are' : ' is'} waiting for your confirmation.`);
    return parts.length ? parts.join(' ') : null;
  }

  async function toggleVoice() {
    if (session.current) {
      session.current.stop();
      session.current = null;
      setLevel(0);
      return;
    }
    setVoiceError(null);
    stopSpeaking();
    const s = new VoiceSession({ onState: setVoice, onLevel: setLevel, onUtterance });
    try {
      await s.start();
    } catch (e) {
      setVoiceError(`Microphone unavailable: ${(e as Error).message}`);
      return;
    }
    session.current = s;
    void hermes.warm(domain).catch(() => {});
    const hello = await greeting();
    if (hello) {
      setMessages(m => [...m, { role: 'assistant', content: hello }]);
      await say(hello);
      s.resume();
    }
  }

  function toggleWakeWord() {
    const next = !wakeWord;
    setWakeWord(next);
    localStorage.setItem('sovereign.wakeWord', next ? '1' : '0');
  }

  async function ingested(r: IngestResult) {
    await onKnowledgeChanged();
    onHighlight(new Set(r.touched_uids));
    const parts = r.duplicate ? ['already in the graph, nothing new'] : [
      `${r.facts_created} new facts`, r.facts_strengthened && `${r.facts_strengthened} strengthened`,
      r.facts_invalidated && `${r.facts_invalidated} superseded`, r.facts_review && `${r.facts_review} to review`,
      r.facts_rejected && `${r.facts_rejected} rejected (the text doesn't support them)`,
      `${r.entities_created} new entities`,
    ].filter(Boolean);
    setMessages(m => [...m, { role: 'assistant', content: `**Source stored** through the pipeline: ${parts.join(' · ')}.` }]);
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
            <button onClick={toggleVoiceOut} className={`rounded p-1 ${voiceOut ? 'text-accent' : 'text-faint hover:text-ink'}`} title="Read typed answers aloud">
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

      <div className="relative min-h-0 flex-1">
      {attaching && (
        <div className="absolute inset-0 z-10 flex flex-col bg-bg">
          <div className="flex items-center gap-2 border-b border-line px-4 py-2">
            <Paperclip size={12} className="text-dim" />
            <span className="flex-1 text-[11px] text-dim">Add a source — the pipeline extracts its facts and checks each against the text</span>
            <button onClick={() => setAttaching(false)} className="rounded p-1 text-faint hover:text-ink" title="Back to the conversation"><X size={13} /></button>
          </div>
          <div className="min-h-0 flex-1"><IngestPanel domain={domain} onIngested={ingested} ontologyReady={ontologyReady} /></div>
        </div>
      )}
      <div ref={scroller} className="h-full space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !busy && (
          <div className="mt-10 space-y-2 text-center text-xs text-faint">
            <p className="font-mono tracking-widest text-dim">HEAD AGENT</p>
            <p>"What do we know about …?"</p>
            <p>📎 paste an article to learn it</p>
            <p>"What's the biggest risk right now?"</p>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="ml-8 rounded-lg bg-panel-2 px-3 py-2 text-[13px] whitespace-pre-wrap">{m.content}</div>
          ) : (
            <div key={i} className={`border-l-2 pl-3 ${m.error ? 'border-bad/60' : 'border-accent/60'}`}>
              {m.steps && m.steps.length > 0 && <Steps steps={m.steps} onOpenBriefing={onOpenBriefing} />}
              {m.error ? <p className="text-[13px] text-bad">{m.content}</p> : <Markdown>{m.content}</Markdown>}
            </div>
          ),
        )}

        {busy && (
          <div className="border-l-2 border-accent/60 pl-3">
            {steps.length > 0 && <Steps steps={steps} onOpenBriefing={onOpenBriefing} />}
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
      </div>

      {(voice !== 'off' || voiceError) && (
        <div className="flex items-center gap-2 border-t border-line px-3 py-2 text-[11.5px]">
          {voice !== 'off' && (
            <>
              <AudioLines size={14} className={voice === 'hearing' ? 'text-bad' : voice === 'speaking' ? 'text-accent' : 'text-dim'} />
              <span className="text-dim">{VOICE_LABEL[voice]}{wakeWord && voice === 'listening' ? ' for “Sovereign, …”' : ''}</span>
              <span className="h-1.5 w-16 overflow-hidden rounded bg-line">
                <span className="block h-1.5 rounded bg-accent transition-[width] duration-75" style={{ width: `${Math.round(level * 100)}%` }} />
              </span>
              {heard && <span className="min-w-0 flex-1 truncate text-faint" title={heard}>“{heard}”</span>}
              <button onClick={toggleWakeWord} className={`ml-auto shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${wakeWord ? 'border-accent/60 text-accent' : 'border-line-2 text-faint hover:text-ink'}`}
                title="Only answer when addressed: “Sovereign, …” / “자비스, …” (follow-ups within 30 s need no wake word)">
                wake word
              </button>
            </>
          )}
          {voiceError && <span className="truncate text-bad">{voiceError}</span>}
        </div>
      )}

      <form
        onSubmit={e => { e.preventDefault(); void send(input); }}
        className="flex items-end gap-2 border-t border-line p-3"
      >
        <button type="button" onClick={() => setAttaching(a => !a)}
          className={`rounded-md border p-2.5 ${attaching ? 'border-accent text-accent' : 'border-line-2 text-dim hover:text-ink'}`}
          title="Add a source: paste an article, notes or a report — its facts are learned into the graph">
          <Paperclip size={15} />
        </button>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input); } }}
          rows={2}
          placeholder={voice !== 'off' ? 'Talk, or type…' : 'Ask the Head Agent…  (Enter to send)'}
          className="min-h-[44px] flex-1 resize-none rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent/60"
        />
        <button
          type="button"
          onClick={() => void toggleVoice()}
          className={`rounded-md border p-2.5 ${voice !== 'off' ? 'border-accent text-accent' : 'border-line-2 text-dim hover:text-ink'}`}
          title={voice !== 'off' ? 'End the voice conversation' : 'Talk with the Head Agent (local speech; nothing leaves this machine)'}
        >
          {voice !== 'off' ? <MicOff size={15} /> : <Mic size={15} />}
        </button>
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()} className="rounded-md border border-line-2 p-2.5 text-dim hover:text-bad" title="Stop">
            <Square size={15} />
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()} className="rounded-md bg-accent p-2.5 text-black disabled:opacity-30" title="Send">
            <Send size={15} />
          </button>
        )}
      </form>
    </div>
  );
}
