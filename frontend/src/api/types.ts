export interface Model {
  id: string;
  name: string;
  ready: boolean;
  configured: boolean;
  loaded?: boolean;
  detail?: string;
}

export interface Job {
  id: string;
  model_id: string;
  status: string;
  error?: string;
  audio_url?: string;
  runtime_seconds?: number | null;
  started_at?: number | null;
}

export interface Evaluation {
  status: string;
  error?: string;
  errors?: { name: string; reason: string }[];
  metrics: { name: string; value: number; evaluator?: string }[];
}

export const defaults: Model[] = [
  { id: 'qwen3', name: 'Qwen3-TTS (1.7B)', ready: false, configured: false },
  { id: 'cosyvoice3', name: 'CosyVoice 3 (0.5B)', ready: false, configured: false },
  { id: 'xtts-v2', name: 'XTTS-v2', ready: false, configured: false },
];

