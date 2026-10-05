class PlayerLevelProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.windowFrames = options.processorOptions.windowFrames;
    this.frames = 0; this.sum = 0; this.samples = 0;
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      for (const channel of channels) { this.sum += channel[i] * channel[i]; this.samples++; }
      this.frames++;
      if (this.frames === this.windowFrames) {
        this.port.postMessage({ rms: Math.sqrt(this.sum / this.samples), time: (currentFrame + i + 1) / sampleRate });
        this.frames = 0; this.sum = 0; this.samples = 0;
      }
    }
    // Web Audio output buffers remain zero-filled; microphone PCM is never copied out.
    return true;
  }
}
registerProcessor('player-level', PlayerLevelProcessor);
