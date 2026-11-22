import { useEffect, useRef, useState } from 'react';
import { AudioLines, Check, Compass, Loader2, Mic, MicOff, Paperclip, Play, Send, Square, Trash2, Volume2, VolumeX, Wrench, X } from 'lucide-react';
import { hermes, kg, type AgentEvent, type CharterState, type ChatTurn, type IngestResult, type Turn } from '../lib/api';
import { IngestPanel } from './IngestPanel';
import { Markdown } from '../components/Markdown';
import { canSpeak, speak, stopSpeaking } from '../lib/voice';
import { VoiceSession, type VoiceState } from '../lib/voiceSession';

/** "Sovereign, …" / "Jarvis, …" — in wake-word mode only addressed utterances (or follow-ups) are answered. */
const WAKE = /^\s*(?:hey\s+)?(?:sovereign|sovren|jarvis)[\s,.!?:~]*/i;
const FOLLOW_UP_MS = 30_000;
const SPOKEN_SENTENCES = 3;  // read aloud; the whole answer stays on screen

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
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  steps?: Step[];
  error?: boolean;
  pending?: boolean;   // the Head is still writing it (asked before a reload): it arrives by itself
}

// The conversation lives in the KG (Hermes saves each question and answer as it happens); this browser keeps a copy
// only to show something before the KG answers, or when it can't.
function loadChat(domain: string): Message[] {
  try { return JSON.parse(localStorage.getItem(`sovereign.chat.${domain}`) ?? '[]'); } catch { return []; }
}

function cacheChat(domain: string, messages: Message[]) {
  try { localStorage.setItem(`sovereign.chat.${domain}`, JSON.stringify(messages.slice(-60))); } catch { /* full: the KG has it */ }
}

// A browser moves its old local chat to the KG at most once per domain (2026-10-14: a chat cleared in one browser came
// back from another browser's copy, which saw an empty KG and imported it again).
const importedKey = (domain: string) => `sovereign.chatImported.${domain}`;
function markImported(domain: string) {
  try { localStorage.setItem(importedKey(domain), '1'); } catch { /* the next load asks again */ }
}

const HISTORY_TURNS = 12;   // the recent turns go to the Head as history (Hermes caps it too)

function fromTurns(turns: ChatTurn[]): Message[] {
  return turns.map(t => ({ role: t.role, content: t.content, steps: (t.steps ?? []) as Step[],
                           error: t.status === 'error', pending: t.status === 'pending' }));
}

function argSummary(args: unknown): string {
  if (!args || typeof args !== 'object') return String(args ?? '');
  const a = args as Record<string, unknown>;
  const main = a.query ?? a.raw_text ?? '';
  return typeof main === 'string' ? main.slice(0, 90) : '';
}

/** What the Head said before a tool: a line or two, the rest on demand (a model sometimes drafts its whole answer). */
function Said({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 160;
  return (
    <div className="text-[12px] leading-snug text-dim [&_p]:my-0">
      <div className={long && !open ? 'line-clamp-2' : ''}><Markdown>{text}</Markdown></div>
      {long && <button onClick={() => setOpen(o => !o)} className="text-[10.5px] text-faint underline hover:text-ink">{open ? 'less' : 'more'}</button>}
    </div>
  );
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <div className="mb-2 space-y-1">
      {steps.map(s => s.said ? (
        <Said key={s.id} text={s.said} />
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

// The Head's tools that change the research charter: the charter card follows them.
const CHARTER_TOOLS = new Set(['draft_charter', 'confirm_charter', 'suggest_charter']);

/** A drafted charter, waiting for the user's yes — the end of the Head's interview. */
function CharterDraft({ state, starting, onStart, onEdit }: {
  state: CharterState;
  starting: boolean;
  onStart: () => void;
  onEdit: () => void;
}) {
  const c = state.charter!;
  return (
    <div className="space-y-2 rounded-md border border-accent/40 bg-panel p-3 text-[12px]">
      <div className="flex items-center gap-1.5 font-mono text-[10px] tracking-widest text-accent uppercase"><Compass size={11} /> Research charter · draft</div>
      <p className="leading-snug text-ink">{c.goal}</p>
      <ol className="list-decimal space-y-0.5 pl-4 text-dim">
        {c.questions.slice(0, 5).map(q => <li key={q}>{q}</li>)}
        {c.questions.length > 5 && <li className="list-none text-faint">+{c.questions.length - 5} more</li>}
      </ol>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button onClick={onStart} disabled={starting} className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-black disabled:opacity-50">
          {starting ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Start researching
        </button>
        <button onClick={onEdit} className="rounded-md border border-line-2 px-3 py-1.5 text-dim hover:text-ink">Edit</button>
        <span className="text-[10.5px] text-faint">or tell the Head what to change</span>
      </div>
    </div>
  );
}

export function AskPanel({ domain, canIngest, onKnowledgeChanged, onHighlight, ontologyReady, onOpenCharter }: {
  domain: string;
  canIngest: boolean;   // editors add sources; any member asks
  onKnowledgeChanged: () => Promise<void>;
  onHighlight: (uids: Set<string>) => void;
  ontologyReady: boolean;
  onOpenCharter: () => void;
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
  const audio = useRef<{ el: HTMLAudioElement; url: string } | null>(null);   // a typed answer read aloud
  const audioSeq = useRef(0);   // bumped by every stop: a read-aloud still waiting for its audio sees it
  const lastAnswerAt = useRef(0);
  const wakeRef = useRef(wakeWord);
  const messagesRef = useRef(messages);
  const scroller = useRef<HTMLDivElement>(null);
  wakeRef.current = wakeWord;
  messagesRef.current = messages;

  useEffect(() => { cacheChat(domain, messages); }, [domain, messages]);

  // What the user wants from this domain: none yet → the Head interviews; a draft → the card to start research.
  const [charter, setCharter] = useState<CharterState | null>(null);
  const [starting, setStarting] = useState(false);
  const loadCharter = () => kg.charter(domain).then(setCharter).catch(() => undefined);
  useEffect(() => { void loadCharter(); }, [domain]);

  async function startResearch() {
    setStarting(true);
    try {
      const s = await kg.confirmCharter(domain);
      setCharter(s);
      const runs = s.kickoff?.jobs?.length ?? 0;
      const note = s.kickoff?.error
        ? `**Charter confirmed.** The research service didn't take the first runs (${s.kickoff.error}).`
        : `**Research started** from the charter: ${runs} run${runs === 1 ? '' : 's'} queued — a broad first study and one per core question. ` +
          'They work in the background (the Researcher on the left); the graph fills as they read.';
      setMessages(m => [...m, { role: 'assistant', content: note }]);
      void kg.addChat(domain, { role: 'assistant', content: note }).catch(() => undefined);
    } catch (e) {
      setMessages(m => [...m, { role: 'assistant', content: (e as Error).message, error: true }]);
    } finally {
      setStarting(false);
    }
  }

  // The conversation from the KG; this browser's old local one is moved there once.
  const waiting = messages.some(m => m.pending);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        let turns = await kg.chat(domain);
        const local = loadChat(domain).filter(m => !m.pending);
        if (!turns.length && local.length && !localStorage.getItem(importedKey(domain))) {
          // Light enough to send: the words and what was done, not each tool call's arguments.
          await kg.importChat(domain, local.slice(-400).map(m => ({
            role: m.role, content: m.content, error: m.error,
            steps: (m.steps ?? []).map(({ args: _args, ...step }) => step),
          })));
          turns = await kg.chat(domain);
        }
        markImported(domain);   // from now on the KG is the conversation, even when it is empty
        if (alive && !abort.current) setMessages(fromTurns(turns));
      } catch { /* the KG is unreachable: the local copy stays */ }
    })();
    return () => { alive = false; };
  }, [domain]);
  // An answer still being written (asked before a reload): look again until it lands.
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      const turns = await kg.chat(domain).catch(() => null);
      if (turns && !abort.current) setMessages(fromTurns(turns));
    }, 3000);
    return () => clearInterval(t);
  }, [domain, waiting]);

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

  useEffect(() => () => { abort.current?.abort(); session.current?.stop(); stopSpeaking(); stopAudio(); }, []);

  async function send(text: string, spoken = false, onFirstTool?: () => void): Promise<Message | null> {
    text = text.trim();
    if (!text || abort.current) return null;
    const history: Turn[] = messagesRef.current.filter(m => !m.error && !m.pending).slice(-HISTORY_TURNS)
      .map(m => ({ role: m.role, content: m.content }));
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
          }
          setSteps([...collected]);
          if (CHARTER_TOOLS.has(e.name)) {
            void loadCharter();
            void onKnowledgeChanged();
          }
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
    stopAudio();
    const seq = audioSeq.current;
    let blob: Blob;
    try {
      blob = await hermes.speak(text);
    } catch {
      if (seq === audioSeq.current) speak(text);
      return;
    }
    // Stopped while it was being synthesized (voice off, a voice session, another domain): it must not start now.
    if (seq !== audioSeq.current) return;
    const url = URL.createObjectURL(blob);
    const el = new Audio(url);
    audio.current = { el, url };
    el.onended = () => { if (audio.current?.el === el) stopAudio(); };
    try {
      await el.play();
    } catch {
      if (audio.current?.el === el) { stopAudio(); speak(text); }
    }
  }

  /** Stop a typed answer being read aloud and free its audio (stopSpeaking() only reaches the browser's voice); one
   * still being synthesized is dropped when it arrives. */
  function stopAudio() {
    audioSeq.current += 1;
    const a = audio.current;
    if (!a) return;
    audio.current = null;
    a.el.pause();
    URL.revokeObjectURL(a.url);
  }

  // ── Voice conversation ────────────────────────────────────────────────
  async function onUtterance(wav: Blob) {
    const s = session.current;
    if (!s) return;
    let { text } = await hermes.transcribe(wav).catch(e => { setVoiceError((e as Error).message); return { text: '' }; });
    setHeard(text);
    if (!text) return;
    if (wakeRef.current && Date.now() - lastAnswerAt.current > FOLLOW_UP_MS) {
      if (!WAKE.test(text)) return;  // not addressed to Sovereign
    }
    text = text.replace(WAKE, '').trim();
    if (!text) { await say('Yes?'); return; }
    // The Head's tool work takes a while: acknowledge out loud as soon as it starts.
    const ack = hermes.speak('Let me check.').catch(() => null);
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

  /** Once a day, starting voice opens with the day's briefing: the report's spoken summary. */
  async function greeting(): Promise<string | null> {
    const key = `sovereign.greeted.${domain}`;
    const today = new Date().toDateString();
    if (localStorage.getItem(key) === today) return null;
    localStorage.setItem(key, today);
    const reports = await kg.reports(domain, 1).catch(() => null);
    const report = reports?.reports[0];
    const parts: string[] = [];
    if (report && Date.now() - new Date(report.created_at).getTime() < 36 * 3600_000) parts.push(report.spoken || report.headline);
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
    stopAudio();
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
    const note = `**Source stored** through the pipeline: ${parts.join(' · ')}.`;
    setMessages(m => [...m, { role: 'assistant', content: note }]);
    void kg.addChat(domain, { role: 'assistant', content: note }).catch(() => undefined);
  }

  function toggleVoiceOut() {
    const next = !voiceOut;
    setVoiceOut(next);
    localStorage.setItem('sovereign.voiceOut', next ? '1' : '0');
    if (!next) { stopSpeaking(); stopAudio(); }
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
            onClick={() => {
              // The local copy goes with the KG's, so nothing brings the conversation back.
              setMessages([]);
              try { localStorage.removeItem(`sovereign.chat.${domain}`); } catch { /* nothing kept */ }
              markImported(domain);
              void kg.clearChat(domain).catch(() => undefined);
            }}
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
        {messages.length === 0 && !busy && (charter?.status === 'none' && canIngest ? (
          <div className="mx-auto mt-10 max-w-sm space-y-2 text-center text-xs text-faint">
            <p className="font-mono tracking-widest text-dim">HEAD AGENT</p>
            <p className="text-[13px] leading-relaxed text-dim">What do you want to find out about this topic?</p>
            <p className="leading-relaxed">Tell me in your own words. I'll ask a few questions, write a research charter with you, and
              start researching once you agree — then keep at it.</p>
          </div>
        ) : (
          <div className="mt-10 space-y-2 text-center text-xs text-faint">
            <p className="font-mono tracking-widest text-dim">HEAD AGENT</p>
            <p>"What do we know about …?"</p>
            {canIngest && <p>📎 paste an article to learn it</p>}
            <p>"Where do the sources disagree?"</p>
          </div>
        ))}

        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="ml-8 rounded-lg bg-panel-2 px-3 py-2 text-[13px] whitespace-pre-wrap">{m.content}</div>
          ) : (
            <div key={i} className={`border-l-2 pl-3 ${m.error ? 'border-bad/60' : 'border-accent/60'}`}>
              {m.steps && m.steps.length > 0 && <Steps steps={m.steps} />}
              {m.pending ? (
                <p className="flex items-center gap-2 text-xs text-faint">
                  <Loader2 size={12} className="animate-spin" /> The Head is still answering — it appears here when done.
                </p>
              ) : m.error ? <p className="text-[13px] text-bad">{m.content}</p> : <Markdown>{m.content}</Markdown>}
            </div>
          ),
        )}

        {!busy && canIngest && charter?.status === 'draft' && charter.charter && (
          <CharterDraft state={charter} starting={starting} onStart={() => void startResearch()} onEdit={onOpenCharter} />
        )}

        {busy && (
          <div className="border-l-2 border-accent/60 pl-3">
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
      </div>

      {canIngest && charter?.status === 'active' && charter.suggestions.length > 0 && (
        <button onClick={onOpenCharter} className="flex items-center gap-1.5 border-t border-line px-4 py-1.5 text-left text-[11px] text-dim hover:text-ink">
          <Compass size={11} className="text-accent" />
          The Head suggested {charter.suggestions.length} addition{charter.suggestions.length === 1 ? '' : 's'} to the research charter — review
        </button>
      )}

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
                title="Only answer when addressed: “Sovereign, …” / “Jarvis, …” (follow-ups within 30 s need no wake word)">
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
        {canIngest && (
          <button type="button" onClick={() => setAttaching(a => !a)}
            className={`rounded-md border p-2.5 ${attaching ? 'border-accent text-accent' : 'border-line-2 text-dim hover:text-ink'}`}
            title="Add a source: paste an article, notes or a report — its facts are learned into the graph">
            <Paperclip size={15} />
          </button>
        )}
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
          <button type="button" onClick={() => { void hermes.stopAsk(domain).catch(() => {}); abort.current?.abort(); }} className="rounded-md border border-line-2 p-2.5 text-dim hover:text-bad" title="Stop">
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
