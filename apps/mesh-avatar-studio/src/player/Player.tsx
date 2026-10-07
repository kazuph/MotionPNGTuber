import { useEffect, useRef, useState } from 'react';
import { createMeshAvatar, type MeshAvatar } from '../engine';
import { localProjects, openLocalProject, type LocalProject } from '../editor/project';
import { PlayerAudio } from './audio';
import { EMOTION_LABELS, initialPlayerState, type PlayerState } from './state';
import './style.css';

async function post(action: string, body: unknown, keepalive = false): Promise<PlayerState> {
  const response = await fetch(`/__studio/player/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), keepalive });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'プレイヤーの操作に失敗しました。');
  return result;
}
export function Player() {
  const obs = location.pathname === '/obs';
  const [client] = useState(() => crypto.randomUUID());
  const [state, setState] = useState<PlayerState>({ ...initialPlayerState });
  const current = useRef(state); current.current = state;
  const [projects, setProjects] = useState<LocalProject[]>([]);
  const [connected, setConnected] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [text, setText] = useState('こんにちは。どこちゃんです。');
  const [busy, setBusy] = useState(false);
  const [motions, setMotions] = useState<MeshAvatar['motions']>([]);
  const canvas = useRef<HTMLCanvasElement>(null), stage = useRef<HTMLDivElement>(null);
  const avatar = useRef<MeshAvatar | null>(null), audio = useRef<PlayerAudio | null>(null);
  const applied = useRef<{ emotion?: string; speaking?: boolean; nonce?: string }>({});
  const pending = useRef<{ voiceLevel: number; audioTime: number; speaking: boolean; sampleRate?: number; windowFrames?: number } | null>(null);
  const sending = useRef(false);
  const active = useRef(false);
  const stopping = useRef(false);
  const generation = useRef(0);
  const control = async (patch: Partial<PlayerState>) => { try { await post('control', { patch }); setError(''); } catch (e) { setError((e as Error).message); } };
  const stopAudio = async () => {
    generation.current++; active.current = false; pending.current = null;
    stopping.current = true; audio.current?.stop(); stopping.current = false; setBusy(false);
    if (current.current.owner === client) { try { await post('audio-stop', { client }); } catch (e) { setError((e as Error).message); } }
  };
  useEffect(() => {
    document.documentElement.classList.add(obs ? 'obs-page' : 'player-page');
    const events = new EventSource(`/__studio/player/events?role=${obs ? 'obs' : 'control'}&client=${client}`);
    events.onopen = () => { setConnected(true); setError(''); };
    events.onerror = () => {
      setConnected(false);
      if (active.current) {
        active.current = false; generation.current++; pending.current = null;
        audio.current?.stop(); setBusy(false);
      }
    };
    events.onmessage = event => {
      const value: PlayerState = JSON.parse(event.data);
      current.current = value; setState(value);
      if (active.current && value.owner !== client) { active.current = false; generation.current++; pending.current = null; audio.current?.stop(); setBusy(false); }
    };
    const leave = () => {
      active.current = false; generation.current++; pending.current = null; audio.current?.stop();
      if (current.current.owner === client) void post('audio-stop', { client }, true).catch(() => undefined);
    };
    window.addEventListener('pagehide', leave);
    return () => { leave(); events.close(); window.removeEventListener('pagehide', leave); document.documentElement.classList.remove('obs-page', 'player-page'); };
  }, [client, obs]);
  useEffect(() => {
    let cancelled = false;
    void localProjects().then(list => {
      if (cancelled) return;
      setProjects(list || []);
      if (!obs && !current.current.project) {
        const name = new URLSearchParams(location.search).get('project') || (list?.some(p => p.name === 'dokochan-bust') ? 'dokochan-bust' : list?.[0]?.name);
        if (name) void post('control', { patch: { project: name } }).catch(e => setError(e.message));
      }
    });
    return () => { cancelled = true; };
  }, [obs]);
  useEffect(() => {
    let cancelled = false; let instance: MeshAvatar | undefined;
    setReady(false); avatar.current = null;
    if (!state.project) return;
    void (async () => {
      const list = await localProjects(), project = list?.find(p => p.name === state.project);
      if (!project) throw new Error('プロジェクトを読み込めません。操作ページで選択してください。');
      const assets = await openLocalProject(project);
      if (cancelled) return;
      instance = await createMeshAvatar(canvas.current!, { rig: assets.rig!, assets: assets.assets, padTop: 0.1, padSide: 0.1 });
      if (cancelled) { instance.destroy(); return; }
      applied.current = {}; avatar.current = instance; setMotions(instance.motions); setReady(true);
    })().catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; instance?.destroy(); if (avatar.current === instance) avatar.current = null; };
  }, [state.project]);
  useEffect(() => {
    const av = avatar.current; if (!av || !ready) return;
    av.setSpeaking(state.speaking); av.setVoiceLevel(state.voiceLevel);
    if (applied.current.emotion !== state.emotion || applied.current.speaking !== state.speaking) av.setEmotion(state.emotion, { playMotion: false });
    av.setPointer(state.followPointer ? state.pointer : null);
    if (state.motion && applied.current.nonce !== state.motion.nonce) av.play(state.motion.id);
    applied.current = { emotion: state.emotion, speaking: state.speaking, nonce: state.motion?.nonce };
  }, [state, ready]);
  useEffect(() => {
    if (!ready) return;
    let frame = 0;
    const observe = () => {
      const lip = avatar.current?.getLipSyncState();
      if (lip && canvas.current) canvas.current.dataset.mouthOpen = String(lip.open);
      if (canvas.current && audio.current) canvas.current.dataset.microphoneTracks = JSON.stringify(audio.current.getCaptureState());
      frame = requestAnimationFrame(observe);
    };
    observe();
    return () => cancelAnimationFrame(frame);
  }, [ready]);
  useEffect(() => {
    if (obs) return;
    const onLevel = (voiceLevel: number, audioTime: number, speaking: boolean, sampleRate?: number, windowFrames?: number) => {
      if (!active.current || stopping.current) return;
      avatar.current?.setVoiceLevel(voiceLevel); avatar.current?.setSpeaking(speaking);
      pending.current = { voiceLevel, audioTime, speaking, sampleRate, windowFrames };
      if (sending.current) return;
      sending.current = true;
      void (async () => {
        while (pending.current && active.current) {
          const value = pending.current; pending.current = null;
          try { await post('audio-level', { client, ...value }); }
          catch (e) { if (current.current.owner === client && active.current) setError((e as Error).message); break; }
        }
      })().finally(() => { sending.current = false; });
    };
    audio.current = new PlayerAudio(onLevel, () => { void stopAudio(); });
    return () => { active.current = false; audio.current?.stop(); audio.current = null; };
    // The lifecycle is owned by this page's stable controller ID.
  }, [client, obs]);
  const startAudio = async (source: 'mic' | 'tts') => {
    await stopAudio(); const token = generation.current; setError(''); setBusy(true);
    try {
      const value = await post('audio-start', { client, source });
      if (token !== generation.current) return;
      current.current = value; setState(value); active.current = true;
      if (source === 'mic') await audio.current!.microphone(); else await audio.current!.speak(text);
      if (token === generation.current) setBusy(false);
    } catch (e) {
      if (token !== generation.current) return;
      await stopAudio();
      if ((e as Error).name !== 'AbortError') setError(source === 'mic' && (e as Error).name === 'NotAllowedError'
        ? 'マイクが許可されていません。ブラウザのサイト設定とmacOSの「プライバシーとセキュリティ → マイク」で、このブラウザを許可してから再度開始してください。'
        : (e as Error).message);
    }
  };
  const pointerBusy = useRef(false), pointerNext = useRef<[number, number] | null>(null);
  useEffect(() => {
    if (obs) return;
    const move = (event: PointerEvent) => {
      if (!current.current.followPointer || !canvas.current || !avatar.current) return;
      const box = canvas.current.getBoundingClientRect(), point = avatar.current.toImage(event.clientX - box.left, event.clientY - box.top);
      if (!point) return;
      pointerNext.current = [point[0], point[1]];
      if (pointerBusy.current) return;
      pointerBusy.current = true;
      void (async () => { while (pointerNext.current) { const pointer = pointerNext.current; pointerNext.current = null; try { await post('control', { patch: { pointer } }); } catch (e) { setError((e as Error).message); break; } } })().finally(() => { pointerBusy.current = false; });
    };
    window.addEventListener('pointermove', move);
    return () => window.removeEventListener('pointermove', move);
  }, [obs]);
  const obsUrl = `${location.origin}/obs`;
  return <main className={obs ? 'obs-stage' : 'player-layout'}>
    <div ref={stage} className="avatar-stage"><canvas ref={canvas} aria-label="配信アバター" data-ready={ready} data-project={state.project || ''} data-emotion={state.emotion} data-speaking={state.speaking} data-voice-level={state.voiceLevel} />{!obs && !ready && <p>プロジェクトを読み込み中…</p>}</div>
    {!obs && <aside className="player-controls">
      <h1>配信・撮影プレイヤー</h1><a href="/">エディターへ</a>
      <p role="status">{connected ? 'OBS同期に接続中' : 'OBS同期の再接続を待っています'} · {ready ? 'アバター準備完了' : '読み込み中'}</p>
      {error && <p role="alert">{error}</p>}
      <label>プロジェクト<select value={state.project || ''} onChange={e => { void stopAudio(); void control({ project: e.target.value }); }}><option value="" disabled>選択してください</option>{projects.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}</select></label>
      <fieldset><legend>感情</legend>{Object.entries(EMOTION_LABELS).map(([id, label]) => <button key={id} aria-pressed={state.emotion === id} onClick={() => void control({ emotion: id as PlayerState['emotion'] })}>{label}</button>)}</fieldset>
      <label><input type="checkbox" checked={state.followPointer} onChange={e => void control({ followPointer: e.target.checked, pointer: null })} />操作ページ内のカーソルを追う</label>
      <button onClick={() => { void stage.current?.requestFullscreen().catch(e => setError(e.message)); }}>全画面</button>
      <label>動作<select defaultValue="" onChange={e => { if (e.target.value) void control({ motion: { id: e.target.value, nonce: crypto.randomUUID() } }); }}><option value="">動作を選ぶ</option>{motions.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
      <section><h2>マイク</h2><button disabled={!ready || busy || state.audioSource !== 'none'} onClick={() => void startAudio('mic')}>マイク開始</button><button disabled={state.owner !== client || state.audioSource !== 'mic'} onClick={() => void stopAudio()}>マイク停止</button><p data-testid="microphone-status" aria-live="polite">{state.audioSource === 'mic' ? busy ? 'マイクを準備中…' : state.speaking ? `マイク使用中 · 入力音量 ${state.voiceLevel.toFixed(3)}` : 'マイク入力を待っています' : 'マイク停止中'}</p><p>マイク音は再生しません。ブラウザのマイク許可が必要です。</p></section>
      <section><h2>Irodori読み上げ</h2><label>読み上げる日本語<textarea value={text} onChange={e => setText(e.target.value)} /></label><button disabled={!ready || busy || state.audioSource !== 'none' || !text.trim()} onClick={() => void startAudio('tts')}>読み上げる</button><button disabled={state.owner !== client || state.audioSource !== 'tts'} onClick={() => void stopAudio()}>読み上げ停止</button><p>{state.audioSource === 'mic' ? 'マイク入力中 · 読み上げ停止中' : busy ? '音声を準備中…' : state.audioSource === 'tts' ? `再生 ${state.audioTime.toFixed(2)} 秒` : '待機中'} · 音量 {state.voiceLevel.toFixed(3)}</p></section>
      <section><h2>OBS URL</h2><a href={obsUrl} target="_blank" rel="noreferrer">{obsUrl}</a><p>OBSのブラウザソースにこのURLを設定します。操作UIなし・透明背景です。操作ページを開いたまま使ってください。</p><p>読み上げ音声は操作ページだけが再生します。OBSのアプリケーション音声キャプチャで操作ページのブラウザを指定してください。マイク録音には音声入力キャプチャを追加します。ブラウザソース自体は音声を再生しません。</p><p>録画はOBSの「録画開始」を使います。音声メーターと短い録画で取り込みを確認してください。操作ページ内のカーソル追従であり、OS全画面追従ではありません。</p></section>
    </aside>}
  </main>;
}
