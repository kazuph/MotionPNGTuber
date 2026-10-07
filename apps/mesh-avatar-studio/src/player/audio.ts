export function bufferRms(buffer: AudioBuffer, seconds: number, windowFrames: number) {
  const end = Math.min(buffer.length, Math.max(0, Math.floor(seconds * buffer.sampleRate)));
  const start = Math.max(0, end - windowFrames);
  let sum = 0;
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = start; i < end; i++) sum += data[i] * data[i];
  }
  return end === start ? 0 : Math.sqrt(sum / ((end - start) * buffer.numberOfChannels));
}
export class PlayerAudio {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: AudioBufferSourceNode | MediaStreamAudioSourceNode | null = null;
  private meter: AudioWorkletNode | null = null;
  private abort: AbortController | null = null;
  private generation = 0;
  private captureTracks: MediaStreamTrack[] = [];
  constructor(private onLevel: (level: number, time: number, speaking: boolean, sampleRate?: number, windowFrames?: number) => void, private onEnd: () => void) {}
  getCaptureState() { return this.captureTracks.map(track => track.readyState); }
  stop() {
    this.generation++;
    this.abort?.abort(); this.abort = null;
    if (this.source instanceof AudioBufferSourceNode) { this.source.onended = null; this.source.stop(); }
    this.source?.disconnect(); this.source = null;
    this.meter?.port.close(); this.meter?.disconnect(); this.meter = null;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    const context = this.context; this.context = null;
    if (context) void context.close();
    this.onLevel(0, 0, false);
  }
  private async open() {
    this.stop();
    const generation = this.generation;
    const context = new AudioContext(); this.context = context;
    await context.resume();
    await context.audioWorklet.addModule(new URL('./level-worklet.js', import.meta.url).href);
    if (generation !== this.generation) throw new DOMException('Audio stopped', 'AbortError');
    const windowFrames = context.createAnalyser().fftSize;
    const meter = new AudioWorkletNode(context, 'player-level', { processorOptions: { windowFrames } });
    this.meter = meter;
    // The meter outputs only silence, keeping capture active without monitoring it.
    meter.connect(context.destination);
    return { context, meter, windowFrames, generation };
  }
  async speak(text: string) {
    const { context, meter, windowFrames, generation } = await this.open();
    const abort = new AbortController(); this.abort = abort;
    const response = await fetch('/__studio/player/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }), signal: abort.signal });
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Irodoriの音声取得に失敗しました。'); }
    const buffer = await context.decodeAudioData(await response.arrayBuffer());
    if (generation !== this.generation) return;
    const source = context.createBufferSource(); source.buffer = buffer; this.source = source;
    source.connect(context.destination); source.connect(meter);
    const started = context.currentTime;
    source.onended = () => { if (generation === this.generation) { this.stop(); this.onEnd(); } };
    source.start();
    meter.port.onmessage = event => {
      if (generation !== this.generation) return;
      const time = Math.min(buffer.duration, Math.max(0, event.data.time - started));
      this.onLevel(bufferRms(buffer, time, windowFrames), time, true, buffer.sampleRate, windowFrames);
    };
  }
  async microphone() {
    const { context, meter, windowFrames, generation } = await this.open();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (generation !== this.generation) { stream.getTracks().forEach(t => t.stop()); return; }
    this.stream = stream;
    this.captureTracks = stream.getTracks();
    const source = context.createMediaStreamSource(stream); this.source = source;
    source.connect(meter);
    const started = context.currentTime;
    meter.port.onmessage = event => {
      if (generation !== this.generation) return;
      this.onLevel(event.data.rms, Math.max(0, event.data.time - started), true, context.sampleRate, windowFrames);
    };
    for (const track of stream.getTracks()) track.addEventListener('ended', () => { if (generation === this.generation) { this.stop(); this.onEnd(); } });
  }
}
