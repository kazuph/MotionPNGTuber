import { createServer, type Server } from 'node:http';
import { beforeAll, afterAll, expect, test } from 'vitest';
import { localProjectMiddleware } from '../src/server/local-projects';
let server: Server, origin: string;
beforeAll(async () => {
  const middleware = localProjectMiddleware(process.cwd());
  server = createServer((req, res) => { void middleware(req, res, () => { res.writeHead(404); res.end(); }); });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterAll(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
const post = (action: string, body: unknown, headers: Record<string, string> = {}) => fetch(`${origin}/__studio/player/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
const state = () => fetch(`${origin}/__studio/player/state`).then(r => r.json());
async function snapshot(reader: ReadableStreamDefaultReader<Uint8Array>) {
  let text = ''; const decoder = new TextDecoder();
  while (!text.includes('\n\n')) { const chunk = await reader.read(); if (chunk.done) throw Error('SSE closed'); text += decoder.decode(chunk.value); }
  return JSON.parse(text.split('\n')[0].slice('data: '.length));
}
test('real local HTTP keeps origin/path/project validation and rejects invalid player payloads', async () => {
  expect((await post('control', { patch: { project: 'dokochan-bust' } })).status).toBe(200);
  expect((await post('control', { patch: { project: '../dokochan-bust' } })).status).toBe(400);
  expect((await post('control', { patch: { emotion: 'other' } })).status).toBe(400);
  expect((await post('control', { patch: { pointer: [null, 3] } })).status).toBe(400);
  expect((await post('control', { patch: { owner: 'other' } })).status).toBe(400);
  expect((await post('tts', { text: '' })).status).toBe(400);
  expect((await post('tts', { text: 'こんにちは' }, { origin: 'https://example.invalid' })).status).toBe(403);
  expect((await fetch(`${origin}/__studio/player/%2e%2e`)).status).toBe(400);
});
test('real SSE snapshots deliver late state and releasing a controller clears real audio ownership', async () => {
  const abort = new AbortController();
  const controller = await fetch(`${origin}/__studio/player/events?role=control&client=first-controller`, { signal: abort.signal });
  const reader = controller.body!.getReader(); await snapshot(reader);
  await post('control', { patch: { emotion: 'happy', followPointer: true, pointer: [640, 300] } });
  await post('audio-start', { client: 'first-controller', source: 'mic' });
  await post('audio-level', { client: 'first-controller', voiceLevel: 0.4, audioTime: 1, speaking: true });
  expect((await post('audio-level', { client: 'other-controller', voiceLevel: 0.4, audioTime: 1, speaking: true })).status).toBe(409);
  expect((await post('audio-level', { client: 'first-controller', voiceLevel: 2, audioTime: 1, speaking: true })).status).toBe(400);
  const obsAbort = new AbortController();
  const obs = await fetch(`${origin}/__studio/player/events?role=obs&client=obs-reader`, { signal: obsAbort.signal });
  const late = await snapshot(obs.body!.getReader());
  expect(late).toMatchObject({ project: 'dokochan-bust', emotion: 'happy', pointer: [640, 300], speaking: true, voiceLevel: 0.4 });
  abort.abort();
  await expect.poll(async () => (await state()).owner).toBeNull();
  expect(await state()).toMatchObject({ voiceLevel: 0, speaking: false, audioSource: 'none' });
  obsAbort.abort();
});
