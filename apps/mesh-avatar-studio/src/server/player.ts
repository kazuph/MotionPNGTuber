import type { IncomingMessage, ServerResponse } from 'node:http';
import { EMOTION_LABELS, initialPlayerState, type PlayerState } from '../player/state';

const IRODORI_URL = 'http://100.80.152.112:8088/api/tts/v1/tts';
const VOICE_LOCK_ID = '5f34b71233d8450895d15e5d70318aa8';
// Specified voice-meeting/say.sh uses curl -m 150.
const TTS_TIMEOUT_MS = 150 * 1000;
class PlayerError extends Error { constructor(public status: number, message: string) { super(message); } }
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new PlayerError(400, 'JSON object required.');
  return value as Record<string, unknown>;
};
const clientId = (value: unknown) => {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9-]+$/.test(value)) throw new PlayerError(400, 'Invalid controller ID.');
  return value;
};
const finite = (value: unknown) => typeof value === 'number' && Number.isFinite(value);
export function createPlayerService() {
  let state: PlayerState = { ...initialPlayerState };
  const streams = new Set<ServerResponse>();
  const send = (res: ServerResponse) => res.write(`data: ${JSON.stringify(state)}\n\n`);
  const update = (patch: Partial<PlayerState>) => { state = { ...state, ...patch }; for (const res of streams) send(res); };
  const stop = () => update({ owner: null, audioSource: 'none', speaking: false, voiceLevel: 0, audioTime: 0 });
  return async (req: IncomingMessage, res: ServerResponse, parts: string[], readBody: (req: IncomingMessage) => Promise<unknown>, projectInfo: (name: string) => Promise<unknown>) => {
    if (parts[0] !== 'player') return false;
    const json = (status: number, value: unknown) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); res.end(JSON.stringify(value)); };
    try {
      if (parts.length !== 2) throw new PlayerError(404, 'Player route not found.');
      const action = parts[1];
      if (req.method === 'GET' && action === 'state') { json(200, state); return true; }
      if (req.method === 'GET' && action === 'events') {
        const url = new URL(req.url!, `http://${req.headers.host}`), role = url.searchParams.get('role');
        if (role !== 'control' && role !== 'obs') throw new PlayerError(400, 'Invalid player role.');
        const client = clientId(url.searchParams.get('client'));
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive', 'X-Content-Type-Options': 'nosniff' });
        streams.add(res); send(res);
        res.once('close', () => { streams.delete(res); if (role === 'control' && state.owner === client) stop(); });
        return true;
      }
      if (req.method !== 'POST') throw new PlayerError(405, 'Method not allowed.');
      const body = object(await readBody(req));
      if (action === 'control') {
        const patch = object(body.patch), next: Partial<PlayerState> = {};
        for (const key of Object.keys(patch)) {
          const value = patch[key];
          if (key === 'project') {
            if (typeof value !== 'string' || !await projectInfo(value)) throw new PlayerError(400, 'Project not available.');
            next.project = value;
          } else if (key === 'emotion') {
            if (typeof value !== 'string' || !Object.hasOwn(EMOTION_LABELS, value)) throw new PlayerError(400, 'Unknown emotion.');
            next.emotion = value as PlayerState['emotion'];
          } else if (key === 'followPointer') {
            if (typeof value !== 'boolean') throw new PlayerError(400, 'Invalid pointer switch.');
            next.followPointer = value;
          } else if (key === 'pointer') {
            if (value !== null && (!Array.isArray(value) || value.length !== 2 || !value.every(finite))) throw new PlayerError(400, 'Invalid pointer.');
            next.pointer = value as PlayerState['pointer'];
          } else if (key === 'motion') {
            const m = object(value);
            if (typeof m.id !== 'string' || typeof m.nonce !== 'string') throw new PlayerError(400, 'Invalid motion.');
            next.motion = { id: m.id, nonce: m.nonce };
          } else throw new PlayerError(400, 'Unknown control field.');
        }
        if (next.project && next.project !== state.project) stop();
        update(next); json(200, state); return true;
      }
      if (action === 'audio-start') {
        const owner = clientId(body.client);
        if (body.source !== 'mic' && body.source !== 'tts') throw new PlayerError(400, 'Unknown audio source.');
        update({ owner, audioSource: body.source, speaking: false, voiceLevel: 0, audioTime: 0 }); json(200, state); return true;
      }
      if (action === 'audio-level' || action === 'audio-stop') {
        const client = clientId(body.client);
        if (state.owner !== client) throw new PlayerError(409, 'Audio control belongs to another page.');
        if (action === 'audio-stop') stop();
        else {
          if (!finite(body.voiceLevel) || (body.voiceLevel as number) < 0 || (body.voiceLevel as number) > 1 || !finite(body.audioTime) || (body.audioTime as number) < 0 || typeof body.speaking !== 'boolean') throw new PlayerError(400, 'Invalid audio state.');
          update({ voiceLevel: body.voiceLevel as number, audioTime: body.audioTime as number, speaking: body.speaking });
        }
        json(200, state); return true;
      }
      if (action === 'tts') {
        if (typeof body.text !== 'string' || !body.text.trim()) throw new PlayerError(400, '読み上げるテキストを入力してください。');
        const abort = new AbortController(), timer = setTimeout(() => abort.abort(), TTS_TIMEOUT_MS);
        const disconnect = () => { if (!res.writableEnded) abort.abort(); }; res.once('close', disconnect);
        try {
          const response = await fetch(IRODORI_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: body.text, voice_lock_id: VOICE_LOCK_ID }), signal: abort.signal });
          if (!response.ok) throw new PlayerError(502, `Irodoriの音声取得に失敗しました（HTTP ${response.status}）。`);
          const wav = Buffer.from(await response.arrayBuffer());
          if (wav.toString('ascii', 0, 4) !== 'RIFF' || wav.toString('ascii', 8, 12) !== 'WAVE') throw new PlayerError(502, 'IrodoriからWAVを取得できませんでした。');
          res.writeHead(200, { 'Content-Type': 'audio/wav', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); res.end(wav);
        } catch (error) {
          if (error instanceof PlayerError) throw error;
          throw new PlayerError(502, abort.signal.aborted ? 'Irodoriの音声取得が中断または時間切れになりました。' : 'Irodoriに接続できません。');
        } finally { clearTimeout(timer); res.off('close', disconnect); }
        return true;
      }
      throw new PlayerError(404, 'Player route not found.');
    } catch (error) {
      if (!(error instanceof PlayerError)) throw error;
      if (!res.destroyed && !res.headersSent) json(error.status, { error: error.message });
      return true;
    }
  };
}
