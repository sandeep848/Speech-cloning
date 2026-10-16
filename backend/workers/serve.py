"""Persistent model-specific inference worker. Run a distinct process per MODEL_ID.
No synthetic audio, external paid APIs, or implicit model substitution.
"""
import io, os, threading, wave
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import Response
from pydantic import BaseModel, Field

MODEL_ID = os.environ.get('MODEL_ID', '')
if MODEL_ID not in ('qwen3','cosyvoice3','xtts-v2'):
    raise RuntimeError('MODEL_ID must be qwen3, cosyvoice3 or xtts-v2')
app=FastAPI(title=f'{MODEL_ID} inference worker')
_lock=threading.Lock()
_engine=None
_error=None

class Request(BaseModel):
    text:str=Field(min_length=1,max_length=2000)
    reference_path:str
    reference_text:str=''
    language:str='English'

def get_engine():
    global _engine, _error
    if _engine is not None:return _engine
    with _lock:
        if _engine is not None:return _engine
        try:
            import torch
            device=os.getenv('MODEL_DEVICE','cuda:0' if torch.cuda.is_available() else 'cpu')
            if MODEL_ID=='qwen3':
                from qwen_tts import Qwen3TTSModel
                _engine=Qwen3TTSModel.from_pretrained(os.getenv('QWEN_MODEL','Qwen/Qwen3-TTS-12Hz-1.7B-Base'),device_map=device,dtype=torch.bfloat16 if device.startswith('cuda') else torch.float32,attn_implementation='eager')
            elif MODEL_ID=='cosyvoice3':
                from cosyvoice.cli.cosyvoice import CosyVoice3
                path=os.getenv('COSY_MODEL_DIR')
                if not path:raise RuntimeError('COSY_MODEL_DIR is required')
                _engine=CosyVoice3(path)
            else:
                from TTS.api import TTS
                _engine=TTS(model_name=os.getenv('XTTS_MODEL','tts_models/multilingual/multi-dataset/xtts_v2')).to(device)
            _error=None
            return _engine
        except Exception as exc:
            _error=str(exc)
            raise

@app.on_event('startup')
def startup():
    if os.getenv('PRELOAD_MODELS','1')=='1':
        try:get_engine()
        except Exception as exc:print(f'Model failed to preload: {exc}',flush=True)

@app.get('/health')
def health(authorization:str|None=Header(None)):
    if os.getenv('WORKER_TOKEN') and authorization!=f"Bearer {os.environ['WORKER_TOKEN']}":raise HTTPException(401,'Unauthorized')
    return {'ready':_engine is not None,'model_id':MODEL_ID,'error':_error}

@app.post('/generate')
def generate(payload:Request, authorization:str|None=Header(None)):
    if os.getenv('WORKER_TOKEN') and authorization!=f"Bearer {os.environ['WORKER_TOKEN']}":raise HTTPException(401,'Unauthorized')
    ref=Path(payload.reference_path).resolve()
    shared=Path(os.getenv('VOICE_STUDIO_DATA','data')).resolve()
    if not ref.is_file() or not ref.is_relative_to(shared/'audio') or ref.suffix.lower()!='.wav':raise HTTPException(422,'Reference path invalid or inaccessible')
    try:
        engine=get_engine()
        import soundfile as sf
        import numpy as np
        with _lock:
            if MODEL_ID=='qwen3':
                # Correct reference transcript improves Qwen zero-shot accuracy.
                kwargs={'text':payload.text,'language':payload.language,'ref_audio':str(ref),'ref_text':payload.reference_text,'non_streaming_mode':True}
                if not payload.reference_text:kwargs['x_vector_only_mode']=True
                result,sr=engine.generate_voice_clone(**kwargs)
                audio=result[0]
            elif MODEL_ID=='cosyvoice3':
                if not payload.reference_text.strip():raise ValueError('CosyVoice zero-shot cloning requires the original audio transcript')
                segments=list(engine.inference_zero_shot(tts_text=payload.text,prompt_text=payload.reference_text,prompt_wav=str(ref),stream=False))
                if not segments:raise RuntimeError('CosyVoice returned no audio')
                audio=np.concatenate([s['tts_speech'].detach().cpu().numpy().reshape(-1) for s in segments])
                sr=engine.sample_rate
            else:
                lang={'English':'en','German':'de','French':'fr','Spanish':'es','Hindi':'hi'}.get(payload.language,payload.language.lower()[:2])
                audio=engine.tts(text=payload.text,speaker_wav=str(ref),language=lang)
                sr=24000
        if hasattr(audio,'detach'):audio=audio.detach().cpu().numpy()
        audio=np.asarray(audio,dtype=np.float32).reshape(-1)
        if not len(audio) or not np.isfinite(audio).all():raise ValueError('Empty or invalid audio')
        output=io.BytesIO()
        sf.write(output,audio,int(sr),format='WAV',subtype='PCM_16')
        return Response(content=output.getvalue(),media_type='audio/wav')
    except ValueError as exc:raise HTTPException(422,str(exc))
    except Exception as exc:raise HTTPException(503,f'{MODEL_ID} inference failed: {exc}')
