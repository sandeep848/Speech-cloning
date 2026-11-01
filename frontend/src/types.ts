export interface Model {
  id: string;
  name: string;
  ready: boolean;
  configured: boolean;
  loaded?: boolean;
  state?: string;
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

export interface MetricDef {
  key: string;
  label: string;
  benchmark: string;
  category: 'Accuracy' | 'Speed & Latency' | 'Acoustic Quality' | 'Signal diagnostics';
  higherIsBetter?: boolean;
  format: (v: number) => string;
  getHealth?: (v: number) => { status: 'excellent' | 'good' | 'warning'; text: string };
}

export interface ReferenceInfo {
  id: string;
  filename: string;
  transcript?: string;
  duration_s?: number;
  sample_rate?: number;
  channels?: number;
}

export interface AgreementData {
  status: string;
  kappa: number | null;
  weighted_kappa?: number | null;
  raters?: string[];
  items?: number;
}

export const DEFAULTS: Model[] = [
  { id: 'qwen3', name: 'Qwen3-TTS (1.7B)', ready: false, configured: false },
  { id: 'cosyvoice3', name: 'CosyVoice 3 (0.5B)', ready: false, configured: false },
  { id: 'xtts-v2', name: 'XTTS-v2', ready: false, configured: false },
];

export const METRIC_DEFS: MetricDef[] = [
  {
    key: 'script_wpm',
    label: 'Script pace',
    category: 'Signal diagnostics',
    benchmark: 'Script words ÷ total audio minutes, including pauses. Not ASR-verified.',
    format: (v) => v.toFixed(1) + ' words/min',
  },
  {
    key: 'quiet_frame_pct',
    label: 'Quiet frames',
    category: 'Signal diagnostics',
    benchmark: '20 ms frames below −40 dBFS RMS; includes quiet speech and pauses.',
    format: (v) => v.toFixed(1) + '%',
  },
  {
    key: 'near_clip_pct',
    label: 'Near-clipping samples',
    category: 'Signal diagnostics',
    benchmark: 'Samples at ≥ 99.9% full scale. A warning indicator, not proof of distortion.',
    format: (v) => v.toFixed(3) + '%',
  },
  {
    key: 'cer',
    label: 'Character Error Rate (CER)',
    benchmark: 'Good: < 1.5% | OK: 1.5%–4.0% | Bad: > 4.0%',
    category: 'Accuracy',
    higherIsBetter: false,
    format: (v) => (v * 100).toFixed(2) + '%',
    getHealth: (v) =>
      v < 0.015
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 0.04
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'wer',
    label: 'Word Error Rate (WER)',
    benchmark: 'Good: < 3.0% | OK: 3.0%–8.0% | Bad: > 8.0%',
    category: 'Accuracy',
    higherIsBetter: false,
    format: (v) => (v * 100).toFixed(2) + '%',
    getHealth: (v) =>
      v < 0.03
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 0.08
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'rtf',
    label: 'Real-Time Factor (RTF)',
    benchmark: 'Good: < 0.80 | OK: 0.80–1.00 | Bad: > 1.00',
    category: 'Speed & Latency',
    higherIsBetter: false,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v < 0.80
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 1.0
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'rtfx',
    label: 'Inverse RTF (RTFx)',
    benchmark: 'Good: > 1.25x | OK: 1.00x–1.25x | Bad: < 1.00x',
    category: 'Speed & Latency',
    higherIsBetter: true,
    format: (v) => v.toFixed(2) + 'x',
    getHealth: (v) =>
      v >= 1.25
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 1.0
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'first_audio_ms',
    label: 'Time to First Audio Chunk',
    benchmark: 'Measured completed audio chunk; excludes model loading',
    category: 'Speed & Latency',
    higherIsBetter: false,
    format: (v) => v.toFixed(0) + ' ms',
    getHealth: (v) =>
      v < 350
        ? { status: 'excellent', text: 'GOOD' }
        : v <= 500
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'ecapa_similarity',
    label: 'Speaker Similarity',
    benchmark: 'Good: > 0.75 | OK: 0.65–0.75 | Bad: < 0.65',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 0.75
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 0.65
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'scoreq',
    label: 'ScoreQ predicted quality',
    benchmark: 'Good: > 4.20 | OK: 3.80–4.20 | Bad: < 3.80',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 4.20
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.80
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'dnsmos_ovrl',
    label: 'DNSMOS (OVRL)',
    benchmark: 'Good: > 3.60 | OK: 3.20–3.60 | Bad: < 3.20',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 3.60
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.20
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'dnsmos_sig',
    label: 'DNSMOS Signal (SIG)',
    benchmark: 'Good: > 3.80 | OK: 3.30–3.80 | Bad: < 3.30',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 3.80
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.30
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
  {
    key: 'dnsmos_bak',
    label: 'DNSMOS Background (BAK)',
    benchmark: 'Good: > 4.00 | OK: 3.50–4.00 | Bad: < 3.50',
    category: 'Acoustic Quality',
    higherIsBetter: true,
    format: (v) => v.toFixed(3),
    getHealth: (v) =>
      v >= 4.00
        ? { status: 'excellent', text: 'GOOD' }
        : v >= 3.50
        ? { status: 'good', text: 'OK' }
        : { status: 'warning', text: 'BAD' },
  },
];

export const PRESET_SCRIPTS_DE = [
  {
    name: 'Daily Routine (Standard Benchmark)',
    text: 'Guten Tag! Mein Name ist Thomas und ich wohne in Berlin. Jeden Tag stehe ich um sieben Uhr auf. Ich trinke einen Kaffee und esse ein Brötchen mit Käse zum Frühstück. Danach fahre ich mit der U-Bahn zur Arbeit. Ich arbeite von acht bis siebzehn Uhr in einem Büro. Am Abend koche ich gerne ein Abendessen für meine Familie oder lese ein Buch. Am Wochenende treffe ich meine Freunde im Park oder gehe im Supermarkt einkaufen. Ich mag mein Leben in Deutschland sehr.',
  },
  {
    name: 'Short Greeting',
    text: 'Hallo, willkommen im Voice Comparison Studio! Heute testen wir modernste neuronale Sprachsynthese und Sprachklonung in Echtzeit.',
  },
  {
    name: 'Technical & Numbers',
    text: 'Im Jahr 1980 wurden über 65.000 Wohnungen gefördert. Die aktuellen Messwerte zeigen eine Latenz von 240 Millisekunden und einen Durchsatz von 1.85-facher Echtzeit.',
  },
  {
    name: 'Narrative Story',
    text: 'In einem kleinen Dorf, umgeben von dichten Wäldern und sanften Bergen, lebte einst ein weiser Erzähler. Seine Geschichten brachten Menschen aus allen Richtungen zusammen, um der Schönheit des Klangs zu lauschen.',
  },
];

export const PRESET_SCRIPTS_EN = [
  {
    name: 'Daily Routine (Standard Benchmark)',
    text: "Good morning! My name is Thomas and I live in Berlin. Every day I wake up at seven o'clock. I drink a coffee and have bread with cheese for breakfast. After that, I take the train to work. I work from eight to five in an office. In the evening, I enjoy cooking dinner for my family or reading a book. On weekends, I meet friends in the park or go shopping at the supermarket. I really enjoy my life here.",
  },
  {
    name: 'Short Greeting',
    text: 'Hello and welcome to the Voice Comparison Studio! Today we are benchmarking state-of-the-art neural speech synthesis and voice cloning in real time.',
  },
  {
    name: 'Technical & Numbers',
    text: 'In the year 1980, over 65,000 apartments were subsidized. The latest measurements show a latency of 240 milliseconds and a throughput of 1.85 times real-time.',
  },
  {
    name: 'Narrative Story',
    text: 'In a small village surrounded by dense forests and gentle mountains, there once lived a wise storyteller. His tales brought people from all directions together to listen to the beauty of sound.',
  },
];
