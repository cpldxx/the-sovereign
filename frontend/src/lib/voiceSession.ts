/**
 * Hands-free voice loop in the browser: the microphone is captured as raw audio, a small voice-activity detector
 * cuts it into utterances (with 0.8 s of pre-roll, so first syllables aren't lost), and each utterance is
 * handed over as a 16 kHz WAV for local transcription. While an answer plays, speaking over it stops the playback
 * and starts a new utterance (barge-in); echo cancellation keeps the answer itself from triggering that.
 */

export type VoiceState = 'off' | 'listening' | 'hearing' | 'working' | 'speaking';

interface Options {
  onState: (state: VoiceState) => void;
  onLevel: (level: number) => void;            // 0..1, for a meter
  onUtterance: (wav: Blob) => Promise<void>;   // transcribe → answer → play; listening resumes after it
}

const TARGET_RATE = 16000;
const PRE_ROLL_S = 0.8;      // covers the start of a word that interrupts playback (detected later, over the answer)
const SILENCE_END_MS = 800;      // this much quiet ends an utterance
const MIN_SPEECH_MS = 350;       // shorter blips are noise
const MAX_UTTERANCE_S = 30;
const BARGE_IN_MS = 250;         // sustained speech over playback that interrupts it

export class VoiceSession {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private state: VoiceState = 'off';
  private ring: Float32Array[] = [];
  private ringSamples = 0;
  private utterance: Float32Array[] = [];
  private speechMs = 0;
  private silenceMs = 0;
  private noise = 0.01;            // running estimate of the room's noise floor (RMS)
  private interruptPlayback: (() => void) | null = null;
  private opts: Options;

  constructor(opts: Options) {
    this.opts = opts;
  }

  get active() { return this.state !== 'off'; }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(2048, 1, 1);
    this.node.onaudioprocess = e => this.process(e.inputBuffer.getChannelData(0));
    this.source.connect(this.node);
    this.node.connect(this.ctx.destination);  // ScriptProcessor only runs when connected (it outputs silence)
    this.setState('listening');
  }

  stop() {
    this.interruptPlayback?.();
    this.node?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach(t => t.stop());
    void this.ctx?.close();
    this.ctx = this.stream = this.node = this.source = null;
    this.utterance = [];
    this.setState('off');
  }

  /** Play an answer; resolves when it ends — or early, if the user starts talking over it. */
  play(audio: Blob): Promise<'ended' | 'interrupted'> {
    if (!this.active) return Promise.resolve('ended');
    return new Promise(resolve => {
      const url = URL.createObjectURL(audio);
      const el = new Audio(url);
      const done = (how: 'ended' | 'interrupted') => {
        el.pause();
        URL.revokeObjectURL(url);
        this.interruptPlayback = null;
        resolve(how);
      };
      this.interruptPlayback = () => done('interrupted');
      el.onended = () => done('ended');
      el.onerror = () => done('ended');
      this.setState('speaking');
      void el.play().catch(() => done('ended'));
    });
  }

  /** A short cue while working ("let me check") — played without leaving the working state, so it can't be
   *  interrupted and doesn't start listening. */
  cue(audio: Blob): Promise<void> {
    if (!this.active) return Promise.resolve();
    return new Promise(resolve => {
      const url = URL.createObjectURL(audio);
      const el = new Audio(url);
      el.onended = el.onerror = () => { URL.revokeObjectURL(url); resolve(); };
      void el.play().catch(() => resolve());
    });
  }

  /** After an answer: back to listening (unless the user already barged in). */
  resume() {
    if (this.state === 'working' || this.state === 'speaking') this.setState('listening');
  }

  private setState(state: VoiceState) {
    this.state = state;
    this.opts.onState(state);
  }

  private process(chunk: Float32Array) {
    if (!this.ctx) return;
    const samples = new Float32Array(chunk);  // the buffer is reused by the browser
    const ms = (samples.length / this.ctx.sampleRate) * 1000;
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.opts.onLevel(Math.min(1, rms * 12));

    const speaking = this.state === 'speaking';
    const threshold = Math.max(this.noise * (speaking ? 5 : 3), speaking ? 0.04 : 0.015);
    const loud = rms > threshold;
    if (!loud && this.state !== 'hearing') this.noise = this.noise * 0.98 + rms * 0.02;

    // Keep a short pre-roll of everything heard.
    this.ring.push(samples);
    this.ringSamples += samples.length;
    while (this.ringSamples - this.ring[0].length > PRE_ROLL_S * this.ctx.sampleRate) {
      this.ringSamples -= this.ring.shift()!.length;
    }

    if (this.state === 'working' || this.state === 'off') return;

    if (this.state === 'speaking') {
      this.speechMs = loud ? this.speechMs + ms : 0;
      if (this.speechMs >= BARGE_IN_MS) {
        this.interruptPlayback?.();
        this.beginUtterance();
      }
      return;
    }

    if (this.state === 'listening') {
      if (loud) {
        this.speechMs += ms;
        if (this.speechMs >= 120) this.beginUtterance();
      } else {
        this.speechMs = 0;
      }
      return;
    }

    // hearing
    this.utterance.push(samples);
    this.speechMs += loud ? ms : 0;
    this.silenceMs = loud ? 0 : this.silenceMs + ms;
    const seconds = this.utterance.reduce((n, c) => n + c.length, 0) / this.ctx.sampleRate;
    if (this.silenceMs >= SILENCE_END_MS || seconds >= MAX_UTTERANCE_S) this.endUtterance();
  }

  private beginUtterance() {
    this.utterance = [...this.ring];
    this.silenceMs = 0;
    this.setState('hearing');
  }

  private endUtterance() {
    const chunks = this.utterance;
    const speech = this.speechMs;
    this.utterance = [];
    this.speechMs = 0;
    if (speech < MIN_SPEECH_MS || !this.ctx) {
      this.setState('listening');
      return;
    }
    this.setState('working');
    const wav = encodeWav(chunks, this.ctx.sampleRate);
    void this.opts.onUtterance(wav).finally(() => this.resume());
  }
}

/** Mono float chunks at `rate` → 16-bit PCM WAV at 16 kHz. */
function encodeWav(chunks: Float32Array[], rate: number): Blob {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const ratio = rate / TARGET_RATE;
  const length = Math.floor(total / ratio);
  const pcm = new Int16Array(length);
  let pos = 0;
  const flat = new Float32Array(total);
  for (const c of chunks) { flat.set(c, pos); pos += c.length; }
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(total, Math.floor((i + 1) * ratio));
    let acc = 0;
    for (let j = start; j < end; j++) acc += flat[j];
    const v = acc / Math.max(1, end - start);
    pcm[i] = Math.max(-1, Math.min(1, v)) * 0x7fff;
  }
  const buffer = new ArrayBuffer(44 + pcm.byteLength);
  const view = new DataView(buffer);
  const text = (offset: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, 'RIFF'); view.setUint32(4, 36 + pcm.byteLength, true); text(8, 'WAVE');
  text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, 'data'); view.setUint32(40, pcm.byteLength, true);
  new Int16Array(buffer, 44).set(pcm);
  return new Blob([buffer], { type: 'audio/wav' });
}
