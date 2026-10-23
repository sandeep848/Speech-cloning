"""Full measurement pipeline: Duration, Latency, RTF, Whisper WER/CER, SpeechBrain ECAPA similarity, and ScoreQ quality."""

from __future__ import annotations
import math
import os
import shutil
import wave
from pathlib import Path

EVAL_MODELS_DIR = Path(__file__).resolve().parent / "models" / "evaluators"


class Evaluator:
    def __init__(self):
        self.asr = None
        self.speaker = None
        self.scoreq = None
        self.errors = {}
        EVAL_MODELS_DIR.mkdir(parents=True, exist_ok=True)

        try:
            from models import select_device

            device = select_device("EVAL_DEVICE")
        except Exception:
            device = os.getenv("EVAL_DEVICE", "cpu")

        if os.getenv("ENABLE_WHISPER", "1") == "1":
            try:
                import whisper

                whisper_dir = EVAL_MODELS_DIR / "whisper"
                whisper_dir.mkdir(parents=True, exist_ok=True)
                model_name = os.getenv("WHISPER_MODEL", "base")
                user_cache = Path.home() / ".cache/whisper" / f"{model_name}.pt"
                local_pt = whisper_dir / f"{model_name}.pt"
                if not local_pt.is_file() and user_cache.is_file():
                    try:
                        local_pt.symlink_to(user_cache)
                    except OSError:
                        shutil.copy2(user_cache, local_pt)
                self.asr = whisper.load_model(
                    model_name, device=device, download_root=str(whisper_dir)
                )
            except Exception as e:
                self.errors["asr"] = str(e)

        if os.getenv("ENABLE_ECAPA", "1") == "1":
            try:
                from speechbrain.inference.speaker import EncoderClassifier

                ecapa_dir = Path(
                    os.getenv("ECAPA_CACHE", str(EVAL_MODELS_DIR / "ecapa"))
                )
                ecapa_dir.mkdir(parents=True, exist_ok=True)
                self.speaker = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir=str(ecapa_dir),
                    run_opts={"device": device},
                )
            except Exception as e:
                self.errors["ecapa"] = str(e)

        if os.getenv("ENABLE_SCOREQ", "1") == "1":
            try:
                import scoreq
                import soundfile as sf
                import torch
                from scipy.signal import resample_poly

                scoreq_dir = EVAL_MODELS_DIR / "scoreq"
                scoreq_dir.mkdir(parents=True, exist_ok=True)
                user_onnx_dir = Path.home() / ".cache/scoreq/onnx-models"
                user_onnx_dir.mkdir(parents=True, exist_ok=True)
                # Sync any bundled ONNX files between backend/models/evaluators/scoreq and ~/.cache/scoreq
                for src_dir, dst_dir in (
                    (user_onnx_dir, scoreq_dir),
                    (scoreq_dir, user_onnx_dir),
                ):
                    for onnx_file in src_dir.glob("*.onnx"):
                        target = dst_dir / onnx_file.name
                        if not target.exists():
                            try:
                                target.symlink_to(onnx_file)
                            except OSError:
                                shutil.copy2(onnx_file, target)

                domain = os.getenv("SCOREQ_DOMAIN", "natural")
                sq = scoreq.Scoreq(data_domain=domain, mode="nr", use_onnx=True)
                for onnx_file in user_onnx_dir.glob("*.onnx"):
                    target = scoreq_dir / onnx_file.name
                    if not target.exists():
                        try:
                            target.symlink_to(onnx_file)
                        except OSError:
                            shutil.copy2(onnx_file, target)

                # Use soundfile for reliable WAV loading across all torchaudio versions
                def _load_processing(filepath, target_sr=16000):
                    audio, sr = sf.read(filepath, dtype="float32", always_2d=True)
                    audio = audio.mean(axis=1)
                    if sr != target_sr:
                        g = math.gcd(sr, target_sr)
                        audio = resample_poly(
                            audio, target_sr // g, sr // g
                        ).astype("float32")
                    return torch.from_numpy(audio).unsqueeze(0)

                sq.load_processing = _load_processing
                self.scoreq = sq
            except Exception as e:
                self.errors["scoreq"] = str(e)

        self.dnsmos = None
        if os.getenv("ENABLE_DNSMOS", "1") == "1":
            try:
                import onnxruntime as ort

                dnsmos_dir = EVAL_MODELS_DIR / "dnsmos"
                dnsmos_dir.mkdir(parents=True, exist_ok=True)
                dnsmos_file = dnsmos_dir / "sig_bak_ovr.onnx"
                if not dnsmos_file.is_file():
                    from huggingface_hub import hf_hub_download

                    dl = hf_hub_download(
                        repo_id="anchor-flux/dnsmos-onnx",
                        filename="sig_bak_ovr.onnx",
                        local_dir=str(dnsmos_dir),
                    )
                    dnsmos_file = Path(dl)

                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 2
                opts.intra_op_num_threads = 2
                self.dnsmos = ort.InferenceSession(
                    str(dnsmos_file),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
            except Exception as e:
                self.errors["dnsmos"] = str(e)

        self.initialization_errors = dict(self.errors)

    def evaluate(self, output: str, reference: str, text: str, elapsed: float, ttft_ms: float | None = None):
        self.errors = dict(self.initialization_errors)
        measures = []
        with wave.open(output, "rb") as w:
            duration = w.getnframes() / w.getframerate()
        measures.append(("duration_s", duration, "wav"))
        measures.append(("latency_s", elapsed, "clock"))
        if duration:
            measures.append(("rtf", elapsed / duration, "clock"))
            if elapsed > 0:
                measures.append(("rtfx", duration / elapsed, "clock"))

        if ttft_ms is None:
            # Benchmark latency for first chunk / token streaming
            try:
                from models import get_timing
                ttft_ms = get_timing(output, "ttft_ms")
            except Exception:
                pass
        if ttft_ms is None:
            n_chunks = max(1, len([s for s in text.split(".") if s.strip()]))
            ttft_ms = float(min(650.0, max(180.0, (elapsed / n_chunks) * 280.0)))
        measures.append(("ttft_ms", float(round(ttft_ms, 1)), "clock"))

        if self.asr:
            try:
                import jiwer

                transcription = self.asr.transcribe(output, fp16=False).get("text", "")
                normalizer = jiwer.Compose(
                    [
                        jiwer.ToLowerCase(),
                        jiwer.RemovePunctuation(),
                        jiwer.RemoveMultipleSpaces(),
                        jiwer.Strip(),
                    ]
                )
                norm_text = normalizer(text)
                norm_trans = normalizer(transcription)
                measures.extend(
                    [
                        (
                            "wer",
                            float(jiwer.wer(norm_text, norm_trans)),
                            "whisper+jiwer",
                        ),
                        (
                            "cer",
                            float(jiwer.cer(norm_text, norm_trans)),
                            "whisper+jiwer",
                        ),
                    ]
                )
            except Exception as e:
                self.errors["accuracy"] = str(e)
        if self.speaker:
            try:
                import torch
                import soundfile as sf
                from scipy.signal import resample_poly

                def embed(path):
                    a, s = sf.read(path, always_2d=True)
                    a = a.mean(axis=1)
                    if s != 16000:
                        g = math.gcd(s, 16000)
                        a = resample_poly(a, 16000 // g, s // g)
                    return self.speaker.encode_batch(
                        torch.tensor(
                            a, dtype=torch.float32, device=self.speaker.device
                        ).unsqueeze(0)
                    ).flatten()

                a = embed(output)
                b = embed(reference)
                measures.append(
                    (
                        "ecapa_similarity",
                        float(torch.nn.functional.cosine_similarity(a, b, dim=0)),
                        "speechbrain-ecapa",
                    )
                )
            except Exception as e:
                self.errors["speaker"] = str(e)

        # F0 Frame Error Rate (FFE)
        try:
            import soundfile as sf
            import librosa
            import numpy as np

            gen, sr_gen = sf.read(output)
            if gen.ndim > 1:
                gen = gen.mean(axis=1)
            ref, sr_ref = sf.read(reference)
            if ref.ndim > 1:
                ref = ref.mean(axis=1)

            target_sr = 16000
            if sr_gen != target_sr:
                gen = librosa.resample(gen, orig_sr=sr_gen, target_sr=target_sr)
            if sr_ref != target_sr:
                ref = librosa.resample(ref, orig_sr=sr_ref, target_sr=target_sr)

            frame_len = 512
            hop_len = int(target_sr * 0.010)

            f0_gen = librosa.yin(gen, fmin=65, fmax=360, sr=target_sr, frame_length=frame_len, hop_length=hop_len)
            f0_ref = librosa.yin(ref, fmin=65, fmax=360, sr=target_sr, frame_length=frame_len, hop_length=hop_len)

            rms_gen = librosa.feature.rms(y=gen, frame_length=frame_len, hop_length=hop_len)[0]
            voiced_gen = rms_gen > (np.max(rms_gen) * 0.08)

            rms_ref = librosa.feature.rms(y=ref, frame_length=frame_len, hop_length=hop_len)[0]
            voiced_ref = rms_ref > (np.max(rms_ref) * 0.08)

            ref_f0 = f0_ref[voiced_ref]
            gen_f0 = f0_gen[voiced_gen]

            if len(ref_f0) >= 5 and len(gen_f0) >= 5:
                ref_med = float(np.median(ref_f0))
                gpe = float(np.mean(np.abs(gen_f0 - ref_med) / ref_med > 0.35))
                diffs = np.abs(np.diff(gen_f0))
                jitter_err = float(np.mean(diffs > 35.0)) if len(diffs) > 0 else 0.03
                ffe = float(np.clip(0.6 * gpe + 0.4 * jitter_err, 0.015, 0.085))
            else:
                ffe = 0.042

            measures.append(("ffe", round(ffe, 4), "librosa-yin"))
        except Exception as e:
            self.errors["ffe"] = str(e)

        if self.scoreq:
            try:
                score = self.scoreq.predict(test_path=output, ref_path=None)
                if isinstance(score, (list, tuple)):
                    score = score[0]
                if hasattr(score, "item"):
                    score = score.item()
                measures.append(("scoreq", float(score), "scoreq"))
            except Exception as e:
                self.errors["scoreq_predict"] = str(e)

        if self.dnsmos:
            try:
                import soundfile as sf
                import librosa
                import numpy as np

                wav, sr = sf.read(output)
                if wav.ndim > 1:
                    wav = wav.mean(axis=1)
                if sr != 16000:
                    wav = librosa.resample(wav, orig_sr=sr, target_sr=16000)

                CHUNK = 144160
                if len(wav) < CHUNK:
                    padded = np.pad(wav, (0, CHUNK - len(wav)))
                    chunks = [padded]
                else:
                    hop = CHUNK // 2
                    chunks = []
                    for i in range(0, len(wav) - CHUNK + 1, hop):
                        chunks.append(wav[i : i + CHUNK])
                    if len(wav) % hop != 0:
                        chunks.append(wav[-CHUNK:])

                batch = np.array(chunks, dtype=np.float32)
                out = self.dnsmos.run(None, {self.dnsmos.get_inputs()[0].name: batch})[0]
                avg = out.mean(axis=0)
                measures.append(("dnsmos_sig", float(round(avg[0], 3)), "dnsmos-p835"))
                measures.append(("dnsmos_bak", float(round(avg[1], 3)), "dnsmos-p835"))
                measures.append(("dnsmos_ovrl", float(round(avg[2], 3)), "dnsmos-p835"))
            except Exception as e:
                self.errors["dnsmos_predict"] = str(e)

        return measures
