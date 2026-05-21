import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketState
from faster_whisper import WhisperModel

from scripts.audio_to_transcript import transcribe_audio_to_text
from scripts.transcript_to_summary import summarize_text
from scripts.summary_to_quiz import generate_quiz
from scripts.lesson_analytics import analyze_transcript, normalize_transcript_payload

app = FastAPI()
logger = logging.getLogger("ml_service")
logging.basicConfig(level=logging.INFO)

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
WHISPER_VAD_FILTER = os.getenv("WHISPER_VAD_FILTER", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_WHISPER_MODEL: Optional[WhisperModel] = None


def _resolve_whisper_model_source(model_name: str) -> str:
    explicit_path = os.getenv("WHISPER_MODEL_PATH")
    if explicit_path:
        return explicit_path

    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    repo_dir = hf_cache / f"models--Systran--faster-whisper-{model_name}" / "snapshots"
    if repo_dir.exists():
        snapshots = sorted([p for p in repo_dir.iterdir() if p.is_dir()])
        if snapshots:
            return str(snapshots[-1])

    return model_name


def _get_model() -> WhisperModel:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel(
            _resolve_whisper_model_source(WHISPER_MODEL_SIZE),
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _WHISPER_MODEL


def _transcribe_wav(path: str):
    model = _get_model()
    segments, _info = model.transcribe(
        path,
        language="ru",
        beam_size=WHISPER_BEAM_SIZE,
        vad_filter=WHISPER_VAD_FILTER,
    )
    return list(segments)


def _convert_to_wav(input_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


async def _send_error(
    ws: WebSocket,
    code: str,
    detail: str,
    status_code: int,
) -> None:
    payload = {
        "type": "error",
        "code": code,
        "detail": detail,
        "status_code": status_code,
    }
    try:
        await ws.send_json(payload)
    except Exception:
        logger.exception("failed to send ML error payload: %s", payload)


def _error_response(code: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "error",
            "code": code,
            "detail": detail,
            "status_code": status_code,
        },
    )


async def _send_partial_status(ws: WebSocket, text: str) -> None:
    await ws.send_json({
        "text": text,
        "start": 0,
        "is_final": False,
    })


async def _run_with_heartbeat(
    ws: WebSocket,
    start_message: str,
    heartbeat_message: str,
    func,
    *args,
    heartbeat_interval_sec: float = 10.0,
):
    await _send_partial_status(ws, start_message)
    task = asyncio.create_task(asyncio.to_thread(func, *args))

    while True:
        done, _pending = await asyncio.wait({task}, timeout=heartbeat_interval_sec)
        if done:
            return await task
        await _send_partial_status(ws, heartbeat_message)


@app.get("/analytics/health")
@app.get("/analytics/health/")
async def analytics_health():
    provider = os.getenv("ANALYTICS_PROVIDER") or os.getenv("LLM_PROVIDER", "heuristic")
    groq_configured = bool(os.getenv("GROQ_API_KEY") or os.getenv("API_KEY_STORAGE"))
    return {
        "status": "ok",
        "analytics_available": True,
        "provider": provider,
        "groq_configured": groq_configured,
        "fallback": "heuristic",
    }


@app.post("/analytics/analyze")
async def analyze_lesson(payload: Any = Body(...)):
    try:
        transcript = normalize_transcript_payload(payload)
    except ValueError as exc:
        return _error_response("VALIDATION_ERROR", str(exc), 400)

    try:
        analytics = await asyncio.to_thread(analyze_transcript, transcript)
    except Exception:
        logger.exception("lesson analytics failed")
        return _error_response(
            "ANALYTICS_FAILED",
            "Не удалось сформировать аналитику занятия.",
            500,
        )

    return {"analytics": analytics}


@app.websocket("/transcriber/ws/transcribe")
async def ws_transcribe(ws: WebSocket):
    await ws.accept()
    tmp_input = None
    tmp_wav = None
    chunk_count = 0
    try:
        # Wait for init from client
        init_msg = await ws.receive_text()
        try:
            init_payload = json.loads(init_msg)
        except Exception:
            logger.warning("invalid init payload: not json")
            await _send_error(ws, "INVALID_INIT", "Некорректный init payload", 400)
            await ws.close()
            return
        if init_payload.get("type") != "init":
            logger.warning("invalid init payload: missing type=init")
            await _send_error(ws, "INVALID_INIT", "Ожидалось сообщение type=init", 400)
            await ws.close()
            return
        await ws.send_json({"type": "init_ack"})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
            tmp_input = tmp.name

        while True:
            msg = await ws.receive()
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                chunk_count += 1
                with open(tmp_input, "ab") as f:
                    f.write(data)

                # Send a lightweight partial update every 10 chunks
                if chunk_count % 10 == 0:
                    await ws.send_json({
                        "text": f"[partial] received {chunk_count} chunks",
                        "start": 0,
                        "is_final": False,
                    })
            elif msg.get("text") is not None:
                text = msg["text"]
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = None
                if payload and payload.get("type") in {"end", "cancel"}:
                    break
                if payload and payload.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
                if text.strip().upper() == "END":
                    break

        # Convert to wav for stable decoding
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp_wav = tmp.name
        try:
            await asyncio.to_thread(_convert_to_wav, tmp_input, tmp_wav)
        except subprocess.CalledProcessError:
            logger.exception("ffmpeg conversion failed")
            await _send_error(
                ws,
                "MEDIA_CONVERSION_FAILED",
                "Не удалось преобразовать загруженный файл в аудио.",
                400,
            )
            return

        try:
            segments = await _run_with_heartbeat(
                ws,
                "[partial] transcribing...",
                "[partial] transcribing...",
                _transcribe_wav,
                tmp_wav,
            )
        except Exception:
            logger.exception("transcription failed")
            await _send_error(
                ws,
                "TRANSCRIPTION_FAILED",
                "Сервис распознавания временно недоступен.",
                503,
            )
            return
        full_text = []
        transcript_items = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            full_text.append(text)
            transcript_items.append({
                "start_ms": int(seg.start * 1000),
                "end_ms": int(seg.end * 1000),
                "text": text,
            })
            await ws.send_json({
                "text": text,
                "start": int(seg.start * 1000),
                "is_final": True,
            })

        transcript_text = " ".join(full_text).strip()
        if not transcript_text:
            logger.warning("transcription produced empty transcript")
            await _send_error(
                ws,
                "EMPTY_TRANSCRIPT",
                "Не удалось распознать речь в файле.",
                400,
            )
            return
        await ws.send_json({
            "text": transcript_text,
            "start": 0,
            "is_final": True,
        })

        try:
            summary_text = await _run_with_heartbeat(
                ws,
                "[partial] generating summary...",
                "[partial] still generating summary...",
                summarize_text,
                transcript_text,
            )
        except Exception:
            logger.exception("summary generation failed")
            await _send_error(
                ws,
                "SUMMARY_FAILED",
                "Не удалось сформировать конспект.",
                500,
            )
            return
        await ws.send_json({"type": "summary", "text": summary_text})

        try:
            quiz_text = await _run_with_heartbeat(
                ws,
                "[partial] generating quiz...",
                "[partial] still generating quiz...",
                generate_quiz,
                summary_text,
            )
        except Exception:
            logger.exception("quiz generation failed")
            await _send_error(
                ws,
                "QUIZ_FAILED",
                "Не удалось сформировать тест.",
                500,
            )
            return
        await ws.send_json({"type": "quiz_text", "text": quiz_text or ""})

        try:
            analytics = await _run_with_heartbeat(
                ws,
                "[partial] generating analytics...",
                "[partial] still generating analytics...",
                analyze_transcript,
                transcript_items,
            )
        except Exception:
            logger.exception("lesson analytics failed")
            await _send_error(
                ws,
                "ANALYTICS_FAILED",
                "Не удалось сформировать аналитику занятия.",
                500,
            )
            return
        await ws.send_json({"type": "analytics", "analytics": analytics})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("unexpected ML websocket error")
        await _send_error(
            ws,
            "UNEXPECTED_ERROR",
            str(e) or "Непредвиденная ошибка ML-сервиса.",
            500,
        )
    finally:
        if tmp_input and os.path.exists(tmp_input):
            os.unlink(tmp_input)
        if tmp_wav and os.path.exists(tmp_wav):
            os.unlink(tmp_wav)
        try:
            if ws.application_state != WebSocketState.DISCONNECTED:
                await ws.close()
        except Exception:
            pass
