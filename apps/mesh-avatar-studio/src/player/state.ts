export const EMOTION_LABELS = { neutral: '通常', happy: '喜び', sad: '悲しみ', angry: '怒り', surprised: '驚き', relaxed: '穏やか' } as const;
export type Emotion = keyof typeof EMOTION_LABELS;
export type AudioSource = 'none' | 'mic' | 'tts';
export interface PlayerState {
  project: string | null;
  emotion: Emotion;
  followPointer: boolean;
  pointer: [number, number] | null;
  voiceLevel: number;
  speaking: boolean;
  audioSource: AudioSource;
  audioTime: number;
  owner: string | null;
  motion: { id: string; nonce: string } | null;
}
export const initialPlayerState: PlayerState = { project: null, emotion: 'neutral', followPointer: false, pointer: null, voiceLevel: 0, speaking: false, audioSource: 'none', audioTime: 0, owner: null, motion: null };
