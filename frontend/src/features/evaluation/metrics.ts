export interface MetricDef {
  key: string;
  label: string;
  benchmark: string;
  category: 'Accuracy' | 'Speed & Latency' | 'Acoustic Quality';
  higherIsBetter?: boolean;
  format: (v: number) => string;
  getHealth: (v: number) => { status: 'excellent' | 'good' | 'warning'; text: string };
}

export const METRIC_DEFS: MetricDef[] = [
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
    label: 'ScoreQ Quality (MOS)',
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

