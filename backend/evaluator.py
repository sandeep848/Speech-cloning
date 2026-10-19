"""Optional real measurement pipeline: no substitute or fictional scores."""

import os, wave, math


class Evaluator:
    def __init__(self):
        self.asr = None
        self.speaker = None
        self.scoreq = None
        self.errors = {}
        device = os.getenv("EVAL_DEVICE", "cpu")
        if os.getenv("ENABLE_WHISPER", "0") == "1":
            try:
                import whisper

                self.asr = whisper.load_model(
                    os.getenv("WHISPER_MODEL", "base"), device=device
                )
            except Exception as e:
                self.errors["asr"] = str(e)
        if os.getenv("ENABLE_ECAPA", "0") == "1":
            try:
                from speechbrain.inference.speaker import EncoderClassifier

                self.speaker = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir=os.getenv("ECAPA_CACHE", "./model_cache/ecapa"),
                    run_opts={"device": device},
                )
            except Exception as e:
                self.errors["ecapa"] = str(e)
        if os.getenv("ENABLE_SCOREQ", "0") == "1":
            try:
                import scoreq

                self.scoreq = scoreq.Scoreq(data_domain="synthetic", mode="nr")
            except Exception as e:
                self.errors["scoreq"] = str(e)

        self.initialization_errors = dict(self.errors)

    def evaluate(self, output: str, reference: str, text: str, elapsed: float):
        self.errors = dict(self.initialization_errors)
        measures = []
        with wave.open(output, "rb") as w:
            duration = w.getnframes() / w.getframerate()
        measures.append(("duration_s", duration, "wav"))
        measures.append(("latency_s", elapsed, "clock"))
        if duration:
            measures.append(("rtf", elapsed / duration, "clock"))
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
                text = normalizer(text)
                transcription = normalizer(transcription)
                measures.extend(
                    [
                        ("wer", float(jiwer.wer(text, transcription)), "whisper+jiwer"),
                        ("cer", float(jiwer.cer(text, transcription)), "whisper+jiwer"),
                    ]
                )
            except Exception as e:
                self.errors["accuracy"] = str(e)
        if self.speaker:
            try:
                import torch, soundfile as sf
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
        if self.scoreq:
            try:
                measures.append(
                    (
                        "scoreq",
                        float(self.scoreq.predict(test_path=output, ref_path=None)),
                        "scoreq",
                    )
                )
            except Exception as e:
                self.errors["scoreq_predict"] = str(e)
        return measures
