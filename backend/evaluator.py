"""Full measurement pipeline: Duration, Latency, RTF, Whisper WER/CER, SpeechBrain ECAPA similarity, and ScoreQ quality."""

from __future__ import annotations
import math
import os
import re
import shutil
import wave
from pathlib import Path

EVAL_MODELS_DIR = Path(
    os.getenv(
        "EVAL_MODELS_DIR",
        str(Path(__file__).resolve().parent / "models" / "evaluators"),
    )
).resolve()


def signal_diagnostics(output: str, text: str):
    """Descriptive measurements, not perceptual quality scores.

    Quiet frames: non-overlapping 20 ms windows below -40 dBFS RMS,
    pooling channel power (no phase cancellation), weighted by sample count.
    Near clipping: channel samples with absolute amplitude >= 0.999.
    Script rate includes pauses and does not verify the spoken transcript.
    """
    import numpy as np
    import soundfile as sf

    quiet_samples = total_frames = near_clip = total_samples = 0
    with sf.SoundFile(output) as audio:
        sample_rate = audio.samplerate
        window = max(1, round(sample_rate * 0.020))
        for block in audio.blocks(blocksize=window, dtype="float64", always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError("Non-finite audio samples")
            total_frames += len(block)
            total_samples += block.size
            near_clip += int(np.count_nonzero(np.abs(block) >= 0.999))
            if float(np.mean(block * block)) < 0.0001:
                quiet_samples += len(block)
    if not total_frames:
        return []
    words = len(re.findall(r"\b\w+(?:['’–-]\w+)*\b", text, flags=re.UNICODE))
    return [
        (
            "script_wpm",
            words * 60 * sample_rate / total_frames,
            "script-words/audio-duration-v1",
        ),
        (
            "quiet_frame_pct",
            quiet_samples * 100 / total_frames,
            "rms-20ms-below-minus40dbfs-v1",
        ),
        ("near_clip_pct", near_clip * 100 / total_samples, "abs-sample-ge-0.999-v1"),
    ]


class Evaluator:
    def __init__(self):
        self.asr = None
        self.speaker = None
        self.scoreq = None
        self.errors = {}
        EVAL_MODELS_DIR.mkdir(parents=True, exist_ok=True)

        # Keep ASR/speaker scoring away from GPU synthesis unless explicitly
        # requested. Metric computation remains unchanged on either device.
        device = os.getenv("EVAL_DEVICE", "cpu")
        if device == "auto":
            from models import select_device

            device = select_device("MODEL_DEVICE")

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
                user_onnx_dir = Path(
                    os.getenv(
                        "SCOREQ_CACHE", str(Path.home() / ".cache/scoreq/onnx-models")
                    )
                )
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
                        audio = resample_poly(audio, target_sr // g, sr // g).astype(
                            "float32"
                        )
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
                    if os.getenv("APP_ENV") == "production":
                        raise RuntimeError(
                            "Provision reviewed DNSMOS weights before production startup"
                        )
                    from huggingface_hub import hf_hub_download

                    dl = hf_hub_download(
                        repo_id="anchor-flux/dnsmos-onnx",
                        revision=os.getenv("DNSMOS_REVISION", "main"),
                        filename="sig_bak_ovr.onnx",
                        local_dir=str(dnsmos_dir),
                    )
                    dnsmos_file = Path(dl)

                import hashlib

                expected = (
                    "269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd"
                )
                if hashlib.sha256(dnsmos_file.read_bytes()).hexdigest() != expected:
                    raise RuntimeError(
                        "DNSMOS checkpoint does not match the reviewed Microsoft model"
                    )
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

    def evaluate(
        self,
        output: str,
        reference: str,
        text: str,
        elapsed: float,
        ttft_ms: float | None = None,
        language: str | None = None,
    ):
        self.errors = dict(self.initialization_errors)
        measures = []
        with wave.open(output, "rb") as w:
            duration = w.getnframes() / w.getframerate()
        measures.append(("duration_s", duration, "wav"))
        measures.append(("latency_s", elapsed, "clock"))
        try:
            measures.extend(signal_diagnostics(output, text))
        except Exception as e:
            self.errors["signal_diagnostics"] = str(e)
        if duration:
            measures.append(("rtf", elapsed / duration, "clock"))
            if elapsed > 0:
                measures.append(("rtfx", duration / elapsed, "clock"))

        # Non-streaming engines do not expose token latency. Only report an
        # observed completed-audio-chunk time when the adapter recorded one.
        from models import get_timing

        first_audio = (
            ttft_ms if ttft_ms is not None else get_timing(output, "first_audio_ms")
        )
        if first_audio is not None:
            measures.append(("first_audio_ms", first_audio, "clock-first-audio-chunk"))

        if self.asr:
            try:
                import jiwer

                asr_options = {"fp16": False}
                if language:
                    asr_options["language"] = {
                        "German": "de",
                        "English": "en",
                        "de": "de",
                        "en": "en",
                    }[language]
                transcription = self.asr.transcribe(output, **asr_options).get(
                    "text", ""
                )
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

        # True FFE requires time-aligned recordings of the same utterance.
        # A cloning reference usually contains different words, so omit FFE.

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

                # Microsoft DNSMOS P.835 non-personalized preprocessing/calibration.
                # Checkpoint identity must be verified against the upstream model.
                chunk = 144160
                if not len(wav):
                    raise ValueError("Empty audio")
                while len(wav) < chunk:
                    wav = np.concatenate((wav, wav))
                hops = int(np.floor(len(wav) / 16000) - 9.01) + 1
                calibrated = []
                raw = []
                for i in range(hops):
                    segment = wav[i * 16000 : i * 16000 + chunk]
                    values = self.dnsmos.run(
                        None,
                        {
                            self.dnsmos.get_inputs()[0].name: np.asarray(
                                segment, dtype=np.float32
                            )[None, :]
                        },
                    )[0][0]
                    raw.append(values)
                    calibrated.append(
                        [
                            np.polyval([-0.08397278, 1.22083953, 0.0052439], values[0]),
                            np.polyval(
                                [-0.13166888, 1.60915514, -0.39604546], values[1]
                            ),
                            np.polyval(
                                [-0.06766283, 1.11546468, 0.04602535], values[2]
                            ),
                        ]
                    )
                for suffix, values in (("_raw", raw), ("", calibrated)):
                    average = np.mean(values, axis=0)
                    for index, name in enumerate(("sig", "bak", "ovrl")):
                        measures.append(
                            (
                                "dnsmos_" + name + suffix,
                                float(average[index]),
                                "dnsmos-p835-reference",
                            )
                        )
            except Exception as e:
                self.errors["dnsmos_predict"] = str(e)

        return measures
