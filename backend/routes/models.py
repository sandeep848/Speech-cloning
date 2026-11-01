"""Models and voice references endpoints."""

from __future__ import annotations
import asyncio
import hashlib
import io
import time
import uuid
import wave
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import artifact_storage
import lifecycle
import security

router = APIRouter(prefix="/api", tags=["models"])


def _get_studio():
    import backend
    return backend


def get_installed_reference(filename: str, text_filename: str):
    st = _get_studio()
    audio_path = st.REFERENCE_DIR / filename
    text_path = st.REFERENCE_DIR / text_filename
    if not audio_path.is_file():
        return None
    try:
        with wave.open(str(audio_path), "rb") as reader:
            dur = round(reader.getnframes() / reader.getframerate(), 2)
            sr = reader.getframerate()
            ch = reader.getnchannels()
    except Exception:
        return None
    data = audio_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    transcript = (
        text_path.read_text(encoding="utf-8").strip() if text_path.is_file() else ""
    )
    with st.db() as c:
        existing = c.execute(
            "SELECT r.id,r.path FROM references_audio r JOIN settings s ON s.key='shared-reference:'||r.id JOIN reference_versions v ON v.reference_id=r.id WHERE r.sha256=? AND v.transcript=? ORDER BY r.created DESC LIMIT 1",
            (digest, transcript),
        ).fetchone()
        if existing and Path(existing["path"]).is_file():
            ref_id = existing["id"]
        else:
            ref_id = str(uuid.uuid4())
            target = st.ROOT / "audio" / f"{ref_id}.wav"
            import shutil
            shutil.copy2(audio_path, target)
            c.execute(
                "INSERT INTO references_audio VALUES (?,?,?,?,?)",
                (ref_id, digest, filename, str(target), time.time()),
            )
        c.execute(
            "INSERT OR IGNORE INTO reference_versions VALUES (?,?,?)",
            (ref_id, transcript, "English" if "_en" in filename else "German"),
        )
        st.mark_shared_reference(c, ref_id)
    return {
        "id": ref_id,
        "filename": filename,
        "transcript": transcript,
        "duration_s": dur,
        "sample_rate": sr,
        "channels": ch,
    }


def store_reference(content, filename, transcript, language):
    st = _get_studio()
    aliases = {
        "German": "German",
        "de": "German",
        "English": "English",
        "en": "English",
    }
    if language not in aliases:
        raise HTTPException(422, "Unsupported reference language")
    language = aliases[language]
    if not filename or not filename.lower().endswith(".wav"):
        raise HTTPException(400, "Reference must be a .wav file")
    if len(transcript) > 4000 or "\x00" in transcript:
        raise HTTPException(422, "Invalid reference transcript")
    if len(content) > 16 * 1024 * 1024:
        raise HTTPException(413, "Reference upload exceeds 16 MiB")
    try:
        with wave.open(io.BytesIO(content), "rb") as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
            channels, width = reader.getnchannels(), reader.getsampwidth()
            if (
                not 8000 <= rate <= 96000
                or channels not in (1, 2)
                or width not in (1, 2, 3, 4)
            ):
                raise ValueError("Unsupported audio format")
            if not 0.25 <= frames / rate <= 120 or reader.getcomptype() != "NONE":
                raise ValueError("Unsupported audio duration or encoding")
            if len(reader.readframes(frames)) != frames * channels * width:
                raise ValueError("Truncated audio")
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise HTTPException(400, "Invalid PCM WAV reference")
    reference_id = str(uuid.uuid4())
    target = st.ROOT / "audio" / f"{reference_id}.wav"
    with st.db() as c:
        c.execute("BEGIN IMMEDIATE")
        artifact_storage.capacity(c, st.ROOT, security.owner(), len(content))
        count = c.execute(
            "SELECT count(*) FROM resource_owners WHERE kind='reference' AND owner_id=?",
            (security.owner(),),
        ).fetchone()[0]
        if count >= 50:
            raise HTTPException(429, "Reference storage quota reached")
        target.write_bytes(content)
        try:
            c.execute(
                "INSERT INTO references_audio VALUES (?,?,?,?,?)",
                (
                    reference_id,
                    hashlib.sha256(content).hexdigest(),
                    "reference.wav",
                    str(target),
                    time.time(),
                ),
            )
            security.claim(c, "reference", reference_id)
            artifact_storage.record(c, target, security.owner(), "reference")
            c.execute(
                "INSERT INTO reference_versions VALUES (?,?,?)",
                (reference_id, transcript.strip(), language),
            )
            c.execute(
                "INSERT INTO user_reference_profiles VALUES (?,?,?,?) ON CONFLICT(owner_id,language) DO UPDATE SET reference_id=excluded.reference_id,transcript=excluded.transcript",
                (security.owner(), language, reference_id, transcript.strip()),
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return get_reference_profile(language)


@router.get("/models")
def models():
    st = _get_studio()
    result = []
    for model in st.MODEL_IDS:
        state = dict(st.model_availability(model))
        if security.production() and state.get("detail"):
            state["detail"] = "Model unavailable; contact the administrator"
        result.append(
            {
                "id": model,
                "name": st.LABELS[model],
                **state,
                "controls": {"temperature": model != "cosyvoice3", "speech_rate": True},
            }
        )
    return result


@router.get("/reference-profile")
def get_reference_profile(language: str = "German"):
    st = _get_studio()
    language = "English" if language.lower() in ("english", "en") else "German"
    with st.db() as c:
        profile = c.execute(
            "SELECT reference_id,transcript FROM user_reference_profiles WHERE owner_id=? AND language=?",
            (security.owner(), language),
        ).fetchone()
        if profile:
            ref = c.execute(
                "SELECT id,filename FROM references_audio WHERE id=?",
                (profile["reference_id"],),
            ).fetchone()
            return {"reference": dict(ref), "language": language}
    if language.lower() in ("english", "en"):
        ref = get_installed_reference("reference_en.wav", "reference_en.txt")
        return {
            "reference": {k: v for k, v in ref.items() if k != "transcript"}
            if ref
            else None,
            "language": "English",
            "expected_audio": "backend/reference/reference_en.wav",
            "expected_text": "backend/reference/reference_en.txt",
        }
    else:
        ref = get_installed_reference("reference.wav", "reference.txt")
        if not ref:
            with st.db() as c:
                row = c.execute(
                    "SELECT reference_id,transcript FROM reference_profiles WHERE id=1"
                ).fetchone()
                if row:
                    try:
                        st.require_reference(c, row["reference_id"])
                    except HTTPException:
                        row = None
                if row:
                    db_ref = c.execute(
                        "SELECT id,filename,path FROM references_audio WHERE id=?",
                        (row["reference_id"],),
                    ).fetchone()
                    if db_ref and Path(db_ref["path"]).exists():
                        ref = {
                            "id": db_ref["id"],
                            "filename": db_ref["filename"],
                            "transcript": row["transcript"] or "",
                        }
        return {
            "reference": {k: v for k, v in ref.items() if k != "transcript"}
            if ref
            else None,
            "language": "German",
            "expected_audio": "backend/reference/reference.wav",
            "expected_text": "backend/reference/reference.txt",
        }


@router.post("/references/upload")
async def upload_reference(
    file: UploadFile = File(...),
    transcript: str = Form(""),
    language: str = Form("German"),
):
    content = await file.read(16 * 1024 * 1024 + 1)
    return await asyncio.to_thread(
        store_reference, content, file.filename, transcript, language
    )


@router.get("/references/{reference_id}/audio")
def reference_audio(reference_id: str):
    st = _get_studio()
    with st.db() as c:
        st.require_reference(c, reference_id)
        row = c.execute(
            "SELECT path FROM references_audio WHERE id=?", (reference_id,)
        ).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(
            404, "Reference file unavailable. Check server reference configuration."
        )
    return FileResponse(st.stored_audio(row["path"]), media_type="audio/wav")


@router.delete("/references/{reference_id}")
def delete_reference(reference_id: str):
    st = _get_studio()
    with st.db() as c:
        c.execute("BEGIN IMMEDIATE")
        security.require_owner(c, "reference", reference_id)
        paths = lifecycle.remove_reference(c, reference_id)
        discard = lifecycle.discard_unreferenced(c, paths, st.ROOT)
    lifecycle.unlink(discard)
    return {"status": "deleted"}


@router.get("/references")
def list_references():
    st = _get_studio()
    with st.db() as c:
        return [
            dict(row)
            for row in c.execute(
                "SELECT r.id,r.filename,r.created FROM references_audio r JOIN resource_owners o ON o.resource_id=r.id AND o.kind='reference' WHERE o.owner_id=? ORDER BY r.created DESC",
                (security.owner(),),
            )
        ]
