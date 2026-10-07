import { useEffect, useRef, useState } from 'react';
import { AudioLines, Check, Loader2, Mic, MicOff, Send, Square, Trash2, Volume2, VolumeX, Wrench } from 'lucide-react';
import { hermes, kg, type AgentEvent, type Turn } from '../lib/api';
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

    const onEvent = (e: AgentEvent) => {
      switch (e.type) {
        case 'tool_start':
          if (!collected.length) onFirstTool?.();
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
            <button onClick={toggleVoiceOut} className={`rounded p-1 ${voiceOut ? 'text-gold' : 'text-faint hover:text-ink'}`} title="Read typed answers aloud">
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

      {(voice !== 'off' || voiceError) && (
        <div className="flex items-center gap-2 border-t border-line px-3 py-2 text-[11.5px]">
          {voice !== 'off' && (
            <>
              <AudioLines size={14} className={voice === 'hearing' ? 'text-bad' : voice === 'speaking' ? 'text-gold' : 'text-dim'} />
              <span className="text-dim">{VOICE_LABEL[voice]}{wakeWord && voice === 'listening' ? ' for “Sovereign, …”' : ''}</span>
              <span className="h-1.5 w-16 overflow-hidden rounded bg-line">
                <span className="block h-1.5 rounded bg-gold transition-[width] duration-75" style={{ width: `${Math.round(level * 100)}%` }} />
              </span>
              {heard && <span className="min-w-0 flex-1 truncate text-faint" title={heard}>“{heard}”</span>}
              <button onClick={toggleWakeWord} className={`ml-auto shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${wakeWord ? 'border-gold/60 text-gold' : 'border-line-2 text-faint hover:text-ink'}`}
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
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input); } }}
          rows={2}
          placeholder={voice !== 'off' ? 'Talk, or type…' : 'Ask the Head Agent…  (Enter to send)'}
          className="min-h-[44px] flex-1 resize-none rounded-md border border-line-2 bg-bg px-3 py-2 text-[13px] outline-none focus:border-gold/60"
        />
        <button
          type="button"
          onClick={() => void toggleVoice()}
          className={`rounded-md border p-2.5 ${voice !== 'off' ? 'border-gold text-gold' : 'border-line-2 text-dim hover:text-ink'}`}
          title={voice !== 'off' ? 'End the voice conversation' : 'Talk with the Head Agent (local speech; nothing leaves this machine)'}
        >
          {voice !== 'off' ? <MicOff size={15} /> : <Mic size={15} />}
        </button>
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
