"""Synthesis, job orchestration, evaluation execution, and lifecycle operations."""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import TYPE_CHECKING
from fastapi import HTTPException

import artifact_storage
import direct_inference
import lifecycle
import security
from api_schemas import GenerateRequest, ModelControls, BatchRequest
from generation_config import (
    GenerationConfig,
    current as generation_config,
    effective_parameters,
)
from isolated_workers import InferenceCancelled, TerminationFailure

if TYPE_CHECKING:
    import backend as studio
else:
    import sys

    studio = sys.modules.get("backend")

logger = logging.getLogger("studio")


def _get_studio():
    import backend
    return backend


def validate_generated_audio(output: Path):
    if output.stat().st_size > int(os.getenv("MAX_OUTPUT_BYTES", "67108864")):
        raise RuntimeError("Generated audio exceeds storage limit")
    with wave.open(str(output), "rb") as reader:
        count, rate = reader.getnframes(), reader.getframerate()
        if not 8000 <= rate <= 96000 or reader.getnchannels() not in (1, 2):
            raise RuntimeError("Invalid generated audio format")
        if not count or count / rate > float(os.getenv("MAX_OUTPUT_SECONDS", "600")):
            raise RuntimeError("Generated audio exceeds duration limit")
        if (
            len(reader.readframes(count))
            != count * reader.getnchannels() * reader.getsampwidth()
        ):
            raise RuntimeError("Generated audio is truncated")


def update_job(job_id, status, output=None, error=None, runtime_seconds=None):
    st = _get_studio()
    now = time.time()
    with st.db() as c:
        if status not in ("pending", "running"):
            if status == "complete" and output:
                own = c.execute(
                    "SELECT owner_id FROM resource_owners WHERE kind='job' AND resource_id=?",
                    (job_id,),
                ).fetchone()
                if own:
                    artifact_storage.record(c, output, own["owner_id"], "output")
            c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))
        if status == "running":
            c.execute(
                "UPDATE jobs SET status=?,started_at=?,updated=? WHERE id=?",
                (status, now, now, job_id),
            )
        else:
            c.execute(
                "UPDATE jobs SET status=?,output=?,error=?,updated=?,runtime_seconds=? WHERE id=?",
                (status, output, error, now, runtime_seconds, job_id),
            )


def execute_worker(
    job_id,
    model_id,
    text,
    reference_path,
    reference_text,
    language="German",
    config=GenerationConfig(),
):
    st = _get_studio()
    output = st.ROOT / "audio" / f"{job_id}.wav"
    try:
        t0 = time.perf_counter()
        if st.process_isolation():
            output_path, elapsed, first_audio = st.WORKERS.get(model_id).call(
                (text, reference_path, reference_text, str(output), language, config),
                timeout=float(os.getenv("GENERATION_TIMEOUT_SECONDS", "600")),
                cancelled=st.JOB_CANCELLATIONS.get(job_id),
            )
            with st.db() as c:
                c.execute(
                    "INSERT OR REPLACE INTO job_observations VALUES (?,?,?)",
                    (job_id, first_audio, language),
                )
            validate_generated_audio(output)
            return output_path, elapsed
        from models import _request_language

        token = _request_language.set(language)
        config_token = generation_config.set(config)
        from contextlib import nullcontext

        guard = (
            st.REPRODUCIBILITY_LOCK
            if os.getenv("REPRODUCIBLE") == "1"
            else nullcontext()
        )
        try:
            with guard:
                from inference_context import seeded_inference

                with seeded_inference(config.seed):
                    direct_inference.generate_audio(
                        model_id, text, Path(reference_path), reference_text, output
                    )
        finally:
            _request_language.reset(token)
            generation_config.reset(config_token)
        t_elapsed = time.perf_counter() - t0
        from models import get_timing

        with st.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO job_observations VALUES (?,?,?)",
                (job_id, get_timing(output, "first_audio_ms"), language),
            )
        validate_generated_audio(output)
        return str(output), t_elapsed
    except Exception:
        output.unlink(missing_ok=True)
        raise


def evaluate_job(job_id: str):
    st = _get_studio()
    try:
        with st.db() as c:
            c.execute(
                "UPDATE evaluation_runs SET status='running',updated=? WHERE job_id=?",
                (time.time(), job_id),
            )
            row = c.execute(
                "SELECT jobs.output,jobs.runtime_seconds,jobs.created,jobs.updated,batches.text,references_audio.path reference "
                "FROM jobs JOIN batches ON batches.id=jobs.batch_id JOIN references_audio ON batches.reference_id=references_audio.id "
                "WHERE jobs.id=? AND jobs.status=?",
                (job_id, "complete"),
            ).fetchone()
        if not row or not row["output"]:
            raise RuntimeError("Recording missing or job not complete")
        with st.db() as c:
            observation = c.execute(
                "SELECT first_audio_ms,language FROM job_observations WHERE job_id=?",
                (job_id,),
            ).fetchone()
            provenance = c.execute(
                "SELECT configuration FROM job_provenance WHERE job_id=?", (job_id,)
            ).fetchone()
        language = (
            observation["language"]
            if observation
            else json.loads(provenance["configuration"]).get("language", "German")
            if provenance
            else "German"
        )
        first_audio = observation["first_audio_ms"] if observation else None
        evaluator = None if st.process_isolation() else st.get_evaluator()
        with st._evaluator_lock:
            elapsed = (
                row["runtime_seconds"]
                if row["runtime_seconds"] is not None
                else max(0, row["updated"] - row["created"])
            )
            if st.process_isolation():
                metrics, errors = st.WORKERS.get("evaluation").call(
                    (
                        row["output"],
                        row["reference"],
                        row["text"],
                        elapsed,
                        first_audio,
                        language,
                    ),
                    timeout=float(os.getenv("EVALUATION_TIMEOUT_SECONDS", "600")),
                )
            else:
                metrics = evaluator.evaluate(
                    row["output"],
                    row["reference"],
                    row["text"],
                    elapsed,
                    ttft_ms=first_audio,
                    language=language,
                )
                errors = dict(evaluator.errors)
                evaluator.errors.clear()
        with st.db() as c:
            c.execute("DELETE FROM metrics WHERE job_id=?", (job_id,))
            for name, value, method in metrics:
                if not math.isfinite(float(value)):
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO metrics VALUES (?,?,?,?)",
                    (job_id, name, float(value), method),
                )
            c.execute("DELETE FROM evaluation_errors WHERE job_id=?", (job_id,))
            for name, _reason in errors.items():
                c.execute(
                    "INSERT OR REPLACE INTO evaluation_errors VALUES (?,?,?)",
                    (job_id, name, "Evaluator unavailable; consult server logs"),
                )
            c.execute(
                "UPDATE evaluation_runs SET status=?,error=?,updated=? WHERE job_id=?",
                ("partial" if errors else "completed", None, time.time(), job_id),
            )
    except Exception:
        logger.exception("Evaluation failed job=%s", job_id)
        with st.db() as c:
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=excluded.error,updated=excluded.updated",
                (
                    job_id,
                    "failed",
                    "Evaluation failed; contact the administrator with the job ID",
                    time.time(),
                ),
            )
    finally:
        with st._eval_jobs_lock:
            st._evaluation_jobs.discard(job_id)


def schedule_evaluation(job_id: str, force: bool = False) -> str:
    st = _get_studio()
    with st._eval_jobs_lock:
        with st.db() as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Unknown job")
            if row["status"] != "complete":
                raise HTTPException(409, "Generation not complete")
            existing = c.execute(
                "SELECT status FROM evaluation_runs WHERE job_id=?", (job_id,)
            ).fetchone()
            if job_id in st._evaluation_jobs:
                return "running"
            if (
                not force
                and existing
                and existing["status"] in ("completed", "partial")
            ):
                return existing["status"]
            if len(st._evaluation_jobs) >= st.MAX_PENDING:
                raise HTTPException(429, "Evaluation queue is full")
            c.execute(
                "INSERT INTO evaluation_runs VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,error=NULL,updated=excluded.updated",
                (job_id, "pending", None, time.time()),
            )
        st._evaluation_jobs.add(job_id)
    st.EVAL_POOL.submit(evaluate_job, job_id)
    return "scheduled"


def auto_evaluate(job_id):
    st = _get_studio()
    try:
        st.schedule_evaluation(job_id)
    except Exception:
        with st.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO evaluation_runs VALUES (?,?,?,?)",
                (
                    job_id,
                    "failed",
                    "Evaluation failed; contact the administrator with the job ID",
                    time.time(),
                ),
            )


async def run_job(
    job_id,
    model,
    text,
    path,
    reference_text,
    language="German",
    config=GenerationConfig(),
):
    st = _get_studio()
    future = None
    try:
        async with st.MODEL_LOCKS[model]:
            st.update_job(job_id, "running")
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                st.POOL,
                st.execute_worker,
                job_id,
                model,
                text,
                path,
                reference_text,
                language,
                config,
            )
            output, runtime = await asyncio.shield(future)
            st.update_job(job_id, "complete", output, runtime_seconds=runtime)
            if os.environ.get("AUTO_EVALUATE", "1") == "1":
                auto_evaluate(job_id)
    except asyncio.CancelledError:
        st.JOB_CANCELLATIONS[job_id].set()
        if future is not None:
            try:
                await asyncio.shield(future)
            except TerminationFailure:
                logger.critical(
                    "Worker could not be reaped job=%s; reservation retained", job_id
                )
                raise
            except Exception:
                pass
        (st.ROOT / "audio" / f"{job_id}.wav").unlink(missing_ok=True)
        st.update_job(job_id, "cancelled", error="Generation cancelled")
        raise
    except TerminationFailure:
        logger.critical(
            "Worker termination failed job=%s; admission remains reserved", job_id
        )
        with st.db() as c:
            c.execute(
                "UPDATE jobs SET error='Worker termination needs administrator intervention',updated=? WHERE id=?",
                (time.time(), job_id),
            )
    except InferenceCancelled:
        st.update_job(job_id, "cancelled", error="Generation cancelled")
    except TimeoutError:
        st.update_job(
            job_id, "failed", error="Generation exceeded its configured time limit"
        )
    except Exception:
        logger.exception("Inference failed job=%s", job_id)
        st.update_job(
            job_id,
            "failed",
            error="Inference failed; contact the administrator with the job ID",
        )
    finally:
        st.JOB_CANCELLATIONS.pop(job_id, None)


def delete_finished_job(job_id):
    st = _get_studio()
    with st._eval_jobs_lock:
        with st.db() as c:
            c.execute("BEGIN IMMEDIATE")
            security.require_owner(c, "job", job_id)
            paths = lifecycle.remove_job(c, job_id)
            discard = lifecycle.discard_unreferenced(c, paths, st.ROOT)
    lifecycle.unlink(discard)


def apply_retention():
    st = _get_studio()
    days = int(os.getenv("RETENTION_DAYS", "0"))
    if days == 0:
        return
    if not 1 <= days <= 3650:
        raise RuntimeError("RETENTION_DAYS must be 0 (disabled) or 1-3650")
    cutoff = time.time() - days * 86400
    with st._eval_jobs_lock:
        with st.db() as c:
            c.execute("BEGIN IMMEDIATE")
            paths = []
            jobs = c.execute(
                "SELECT j.id FROM jobs j WHERE j.updated<? AND j.status NOT IN ('pending','running') AND NOT EXISTS (SELECT 1 FROM evaluation_runs e WHERE e.job_id=j.id AND e.status IN ('pending','running'))",
                (cutoff,),
            ).fetchall()
            for job in jobs:
                paths.extend(lifecycle.remove_job(c, job["id"]))
            refs = c.execute(
                "SELECT r.id FROM references_audio r WHERE r.created<? AND EXISTS (SELECT 1 FROM resource_owners o WHERE o.kind='reference' AND o.resource_id=r.id) AND NOT EXISTS (SELECT 1 FROM batches b WHERE b.reference_id=r.id) AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='shared-reference:'||r.id)",
                (cutoff,),
            ).fetchall()
            for ref in refs:
                paths.extend(lifecycle.remove_reference(c, ref["id"]))
            discard = lifecycle.discard_unreferenced(c, paths, st.ROOT)
    lifecycle.unlink(discard)


async def retention_loop():
    while True:
        try:
            await asyncio.to_thread(apply_retention)
        except Exception:
            logger.exception("Retention sweep failed")
        await asyncio.sleep(86400)


def resolve_generation_reference(request):
    st = _get_studio()
    req_lang = {"de": "German", "en": "English"}.get(request.language, request.language)
    selected_reference = request.reference_id
    effective_transcript = request.reference_text
    if not selected_reference:
        profile = st.get_reference_profile(req_lang).get("reference")
        if profile:
            selected_reference = profile["id"]
            effective_transcript = profile.get("transcript", "")
    with st.db() as c:
        if not selected_reference:
            if req_lang.lower() in ("english", "en"):
                en_ref = st.get_installed_reference("reference_en.wav", "reference_en.txt")
                if en_ref:
                    selected_reference = en_ref["id"]
                    effective_transcript = en_ref["transcript"]
                else:
                    raise HTTPException(
                        422,
                        "English reference audio not found. Please place reference_en.wav and reference_en.txt in backend/reference/",
                    )
            else:
                profile = c.execute(
                    "SELECT reference_id,transcript FROM reference_profiles WHERE id=1"
                ).fetchone()
                selected_reference = profile["reference_id"] if profile else None
                effective_transcript = profile["transcript"] if profile else ""
        st.require_reference(c, selected_reference)
        ref = c.execute(
            "SELECT * FROM references_audio WHERE id=?", (selected_reference,)
        ).fetchone()
    if not effective_transcript:
        with st.db() as c:
            version = c.execute(
                "SELECT transcript,language FROM reference_versions WHERE reference_id=?",
                (selected_reference,),
            ).fetchone()
        if version:
            effective_transcript = version["transcript"]
    if "cosyvoice3" in request.models and not effective_transcript.strip():
        raise HTTPException(
            422, "CosyVoice requires a transcript for the selected reference"
        )
    if not ref or not Path(ref["path"]).is_file():
        expected_wav = (
            "reference_en.wav"
            if req_lang.lower() in ("english", "en")
            else "reference.wav"
        )
        raise HTTPException(422, f"Configure backend/reference/{expected_wav} first")

    return req_lang, selected_reference, effective_transcript, ref


async def _generate_impl(request: GenerateRequest, deferred=None):
    st = _get_studio()
    if request.temperature is not None and "cosyvoice3" in request.models:
        raise HTTPException(
            422,
            "CosyVoice temperature is unsupported; leave temperature unset for comparison",
        )
    if request.seed is not None and os.getenv("REPRODUCIBLE") != "1":
        raise HTTPException(
            422, "Seeded requests require REPRODUCIBLE=1 (serialized inference)"
        )
    if any(model not in request.models for model in request.model_settings):
        raise HTTPException(422, "Settings were supplied for an unselected model")
    if (
        request.model_settings.get("cosyvoice3", ModelControls()).temperature
        is not None
    ):
        raise HTTPException(422, "CosyVoice temperature is unsupported")
    configurations = {}
    for model in request.models:
        custom = request.model_settings.get(model, ModelControls())
        config = GenerationConfig(
            custom.temperature
            if custom.temperature is not None
            else request.temperature,
            custom.speech_rate
            if custom.speech_rate is not None
            else request.speech_rate,
            request.seed,
        )
        configurations[model] = config
    if not request.text.strip():
        raise HTTPException(422, "Enter a nonempty speech script")
    if len(st.TASKS) + len(request.models) > st.MAX_PENDING:
        raise HTTPException(
            429, "Generation queue is full; retry after current jobs finish"
        )
    if len(set(request.models)) != len(request.models) or any(
        m not in st.MODEL_IDS for m in request.models
    ):
        raise HTTPException(422, "Unsupported or duplicate models")
    req_lang, selected_reference, effective_transcript, ref = await asyncio.to_thread(
        resolve_generation_reference, request
    )

    available = await asyncio.gather(
        *(asyncio.to_thread(st.model_availability, m) for m in request.models)
    )
    if len(st.TASKS) + len(request.models) > st.MAX_PENDING:
        raise HTTPException(429, "Generation queue is full")
    unavailable = [
        st.LABELS[m] + ": " + str(status.get("detail"))
        for m, status in zip(request.models, available)
        if not status["configured"]
    ]
    if unavailable:
        raise HTTPException(
            503,
            "Selected model unavailable; contact the administrator"
            if security.production()
            else "; ".join(unavailable),
        )
    batch_id, planned = await asyncio.to_thread(
        st.plan_generation_jobs,
        request,
        configurations,
        req_lang,
        selected_reference,
        effective_transcript,
        ref,
    )
    for job_id, model, out in planned:
        if out and os.environ.get("AUTO_EVALUATE", "1") == "1":
            st.auto_evaluate(job_id)
        if not out:
            args = (
                job_id,
                model,
                request.text.strip(),
                ref["path"],
                effective_transcript,
                req_lang,
                configurations[model],
            )
            if deferred is not None:
                deferred.append(args)
            else:
                st.submit_job(args)
    return {
        "id": batch_id,
        "jobs": [
            {
                "id": j,
                "model_id": m,
                "status": "complete" if out else "pending",
                "cached": bool(out),
            }
            for j, m, out in planned
        ],
    }


def plan_generation_jobs(
    request, configurations, req_lang, selected_reference, effective_transcript, ref
):
    st = _get_studio()
    now = time.time()
    batch_id = str(uuid.uuid4())
    planned = []
    with st.db() as c:
        c.execute("BEGIN IMMEDIATE")
        outstanding = c.execute(
            "SELECT count(*) FROM jobs WHERE status IN ('pending','running')"
        ).fetchone()[0]
        personal = c.execute(
            "SELECT count(*) FROM jobs j JOIN resource_owners o ON o.resource_id=j.id AND o.kind='job' WHERE o.owner_id=? AND j.status IN ('pending','running')",
            (security.owner(),),
        ).fetchone()[0]
        if outstanding + len(request.models) > st.MAX_PENDING or personal + len(
            request.models
        ) > int(os.getenv("MAX_USER_JOBS", "6")):
            raise HTTPException(429, "Generation capacity reached")
        total = c.execute(
            "SELECT count(*) FROM resource_owners WHERE kind='job' AND owner_id=?",
            (security.owner(),),
        ).fetchone()[0]
        if total + len(request.models) > int(os.getenv("MAX_STORED_JOBS", "1000")):
            raise HTTPException(
                429, "Stored job quota reached; contact the administrator"
            )
        c.execute(
            "INSERT INTO batches VALUES (?,?,?,?)",
            (batch_id, request.text.strip(), selected_reference, now),
        )
        security.claim(c, "generation", batch_id)
        for model in request.models:
            config = configurations[model]
            effective = effective_parameters(model, config)
            key = hashlib.sha256(
                json.dumps(
                    [
                        model,
                        security.owner(),
                        effective,
                        os.getenv("GIT_REVISION", "unknown"),
                        ref["sha256"],
                        request.text.strip(),
                        effective_transcript,
                        req_lang,
                        "native-speed-batching-v3",
                        os.getenv("MODEL_CACHE_VERSION", "6"),
                        {
                            k: v
                            for k, v in os.environ.items()
                            if k.startswith(("QWEN_", "XTTS_", "COSY_"))
                            or k
                            in (
                                "MAX_INTERNAL_PAUSE_MS",
                                "MODEL_DEVICE",
                                "SPEECH_RATE",
                                "COMMA_PAUSE_MS",
                                "SENTENCE_PAUSE_MS",
                                "PARAGRAPH_PAUSE_MS",
                                "CONTINUATION_PAUSE_MS",
                            )
                        },
                        os.getenv("QWEN_MODEL", ""),
                        os.getenv("COSY_MODEL_DIR", ""),
                        os.getenv("XTTS_MODEL", ""),
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            prior = c.execute(
                "SELECT id,output,runtime_seconds FROM jobs WHERE cache_key=? AND status='complete' AND output IS NOT NULL ORDER BY created DESC LIMIT 1",
                (key,),
            ).fetchone()
            if not request.use_cache:
                prior = None
            job_id = str(uuid.uuid4())
            out = prior["output"] if prior and Path(prior["output"]).exists() else None
            c.execute(
                "INSERT INTO jobs (id,batch_id,model_id,status,output,error,created,updated,cache_key,runtime_seconds) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    batch_id,
                    model,
                    "complete" if out else "pending",
                    out,
                    None,
                    now,
                    now,
                    key,
                    prior["runtime_seconds"] if out else None,
                ),
            )
            security.claim(c, "job", job_id)
            if out:
                c.execute(
                    "INSERT INTO job_observations SELECT ?,first_audio_ms,language FROM job_observations WHERE job_id=?",
                    (job_id, prior["id"]),
                )
            if not out:
                artifact_storage.reserve(
                    c,
                    st.ROOT,
                    security.owner(),
                    job_id,
                    int(os.getenv("MAX_OUTPUT_BYTES", "67108864")),
                )
            provenance = {
                "model_id": model,
                "model_revision": os.getenv(
                    {
                        "qwen3": "QWEN_REVISION",
                        "cosyvoice3": "COSY_REVISION",
                        "xtts-v2": "XTTS_REVISION",
                    }[model],
                    "unverified",
                ),
                "reference_sha256": ref["sha256"],
                "transcript_sha256": hashlib.sha256(
                    effective_transcript.encode()
                ).hexdigest(),
                "language": req_lang,
                "temperature": config.temperature,
                "speech_rate": config.speech_rate,
                "effective_parameters": effective,
                "seed": request.seed,
                "software_revision": os.getenv("GIT_REVISION", "unknown"),
                "device": os.getenv("MODEL_DEVICE", "auto"),
                "environment_controls": {
                    k: v
                    for k, v in os.environ.items()
                    if k
                    in (
                        "QWEN_TEMPERATURE",
                        "XTTS_TEMPERATURE",
                        "QWEN_SPEECH_RATE",
                        "XTTS_SPEECH_RATE",
                        "COSY_SPEECH_RATE",
                        "SPEECH_RATE",
                        "COMMA_PAUSE_MS",
                        "SENTENCE_PAUSE_MS",
                        "PARAGRAPH_PAUSE_MS",
                        "CONTINUATION_PAUSE_MS",
                        "MAX_INTERNAL_PAUSE_MS",
                    )
                },
            }
            c.execute(
                "INSERT INTO job_provenance VALUES (?,?)",
                (job_id, json.dumps(provenance)),
            )
            planned.append((job_id, model, out))
    return batch_id, planned


def submit_job(args):
    st = _get_studio()
    job_id = args[0]
    st.JOB_CANCELLATIONS[job_id] = threading.Event()
    task = asyncio.create_task(run_job(*args))
    st.TASKS[job_id] = task

    def finished(done):
        st.TASKS.pop(job_id, None)
        st.JOB_CANCELLATIONS.pop(job_id, None)
        if done.cancelled():
            with st.db() as c:
                c.execute(
                    "UPDATE jobs SET status='cancelled',error='Generation cancelled',updated=? WHERE id=? AND status IN ('pending','running')",
                    (time.time(), job_id),
                )
                c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))

    task.add_done_callback(finished)


async def _batch_experiment_impl(body: BatchRequest):
    st = _get_studio()
    if len(body.models) != len(set(body.models)) or any(
        x not in st.MODEL_IDS for x in body.models
    ):
        raise HTTPException(422, "Unknown model")
    if any(not x.strip() or len(x) > 2000 for x in body.texts):
        raise HTTPException(422, "Invalid script")
    with st.db() as c:
        st.require_reference(c, body.reference_id)
        if not c.execute(
            "SELECT id FROM references_audio WHERE id=?", (body.reference_id,)
        ).fetchone():
            raise HTTPException(404, "Reference not found")
    if len(body.models) * len(body.texts) * body.repetitions + len(st.TASKS) > st.MAX_PENDING:
        raise HTTPException(429, "Batch exceeds available queue capacity")
    parent = str(uuid.uuid4())
    with st.db() as c:
        c.execute(
            "INSERT INTO batch_runs(id,params,created,status) VALUES (?,?,?,'planning')",
            (parent, body.model_dump_json(), time.time()),
        )
        security.claim(c, "batch", parent)
    generated, deferred = [], []
    try:
        for _ in range(body.repetitions):
            for text in body.texts:
                result = await st._generate_impl(
                    GenerateRequest(
                        text=text,
                        reference_id=body.reference_id,
                        models=body.models,
                        reference_text=body.reference_text,
                        language=body.language,
                        use_cache=False,
                    ),
                    deferred=deferred,
                )
                generated.append(result["id"])
                with st.db() as c:
                    c.execute(
                        "INSERT INTO batch_generations VALUES (?,?)",
                        (parent, result["id"]),
                    )
    except BaseException:
        with st.db() as c:
            c.execute("DELETE FROM batch_generations WHERE batch_id=?", (parent,))
            for generation_id in generated:
                jobs = [
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM jobs WHERE batch_id=?", (generation_id,)
                    )
                ]
                for job_id in jobs:
                    c.execute("DELETE FROM storage_reservations WHERE id=?", (job_id,))
                    c.execute("DELETE FROM job_provenance WHERE job_id=?", (job_id,))
                    c.execute("DELETE FROM job_observations WHERE job_id=?", (job_id,))
                    c.execute(
                        "DELETE FROM resource_owners WHERE kind='job' AND resource_id=?",
                        (job_id,),
                    )
                c.execute("DELETE FROM jobs WHERE batch_id=?", (generation_id,))
                c.execute("DELETE FROM batches WHERE id=?", (generation_id,))
                c.execute(
                    "DELETE FROM resource_owners WHERE kind='generation' AND resource_id=?",
                    (generation_id,),
                )
            c.execute("DELETE FROM batch_generations WHERE batch_id=?", (parent,))
            c.execute("DELETE FROM batch_runs WHERE id=?", (parent,))
            c.execute(
                "DELETE FROM resource_owners WHERE kind='batch' AND resource_id=?",
                (parent,),
            )
        raise
    with st.db() as c:
        c.execute("UPDATE batch_runs SET status='ready' WHERE id=?", (parent,))
    for args in deferred:
        st.submit_job(args)
    return {
        "id": parent,
        "generation_ids": generated,
        "total_jobs": len(generated) * len(body.models),
    }


def _batch_generations(batch_id):
    st = _get_studio()
    with st.db() as c:
        rows = c.execute(
            "SELECT generation_id FROM batch_generations WHERE batch_id=? ORDER BY rowid",
            (batch_id,),
        ).fetchall()
    return [st.batch(row["generation_id"]) for row in rows]


def csv_safe(value):
    val = str(value)
    return (
        "'" + val
        if val.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
        else val
    )
