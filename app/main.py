import asyncio
import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional, Set

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Load environment variables from .env (if present)
load_dotenv()

# Serve static assets (if you add any later)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# In-memory task store (MVP only)
TASKS: Dict[str, dict] = {}
TASK_SUBSCRIBERS: Dict[str, Set[WebSocket]] = {}
PUBLISHED_MATERIALS: Dict[str, object] = {
    "task_id": None,
    "summary": [],
    "quiz_text": "",
    "analytics": None,
    "updated_at": None,
}

CHUNK_SIZE = 5
CHUNK_INTERVAL_SEC = 1

ML_WS_URL = os.getenv("ML_WS_URL", "ws://127.0.0.1:8001/transcriber/ws/transcribe")
ML_WS_TIMEOUT_SEC = float(os.getenv("ML_WS_TIMEOUT_SEC", "300"))
INIT_TIMEOUT_SEC = float(os.getenv("INIT_TIMEOUT_SEC", "30"))

logger = logging.getLogger("ws_proxy")
logging.basicConfig(level=logging.INFO)


def _make_transcript_lines(total_lines: int = 50) -> List[dict]:
    # Each item has a start timestamp (seconds) and text
    lines = []
    for i in range(total_lines):
        start_sec = i * 5
        lines.append({
            "start": start_sec,
            "text": f"[Транскрипт] Строка {i + 1}: пример текста лекции.",
        })
    return lines


def _make_summary_lines(total_lines: int = 50) -> List[str]:
    return [f"[Конспект] Пункт {i + 1}: краткое содержание." for i in range(total_lines)]


def _make_test_question() -> dict:
    return {
        "question": "В чем основная идея лекции?",
        "options": [
            "Вариант A",
            "Вариант B",
            "Вариант C",
            "Вариант D",
        ],
        "answer": "Вариант B",
    }


def _is_json_command(text: str) -> bool:
    try:
        payload = json.loads(text)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("type") in {"end", "ping", "cancel", "init"}


def _task_payload(task: dict) -> dict:
    return {
        "id": task["id"],
        "status": task["status"],
        "progress": task["progress"],
        "filename": task["filename"],
        "content_type": task["content_type"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "transcript": task["transcript"],
        "summary": task["summary"],
        "test": task["test"],
        "quiz_text": task.get("quiz_text", ""),
        "analytics": task.get("analytics"),
        "error": task.get("error"),
        "error_code": task.get("error_code"),
        "error_status_code": task.get("error_status_code"),
    }


def _publish_task_materials(task: dict) -> None:
    if not task["summary"] and not task.get("quiz_text"):
        return

    PUBLISHED_MATERIALS["task_id"] = task["id"]
    PUBLISHED_MATERIALS["summary"] = list(task["summary"])
    PUBLISHED_MATERIALS["quiz_text"] = task.get("quiz_text", "")
    PUBLISHED_MATERIALS["analytics"] = task.get("analytics")
    PUBLISHED_MATERIALS["updated_at"] = task["updated_at"]


def _summary_lines_from_text(text: str) -> List[str]:
    return [line.strip("• ").strip() for line in text.splitlines() if line.strip()]


def _error_payload(code: str, detail: str, status_code: int) -> dict:
    return {
        "type": "error",
        "code": code,
        "detail": detail,
        "status_code": status_code,
    }


async def _simulate_processing(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return

    task["status"] = "processing"
    await _broadcast_task(task_id)
    transcript_all = task["_transcript_all"]
    summary_all = task["_summary_all"]

    total_lines = len(transcript_all) if transcript_all else 0
    if total_lines == 0:
        task["status"] = "failed"
        task["error"] = "empty transcript"
        task["updated_at"] = time.time()
        return

    for i in range(0, total_lines, CHUNK_SIZE):
        # add next chunk
        task["transcript"].extend(transcript_all[i:i + CHUNK_SIZE])
        task["summary"].extend(summary_all[i:i + CHUNK_SIZE])

        task["progress"] = min(100, int(((i + CHUNK_SIZE) / total_lines) * 100))
        task["updated_at"] = time.time()
        await _broadcast_task(task_id)

        await asyncio.sleep(CHUNK_INTERVAL_SEC)

    task["status"] = "done"
    task["updated_at"] = time.time()
    await _broadcast_task(task_id)


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/teacher")
async def teacher_index():
    return FileResponse("app/static/index.html")


@app.get("/student")
async def student_index():
    return FileResponse("app/static/student/index.html")


@app.post("/api/upload")
async def upload_audio(file: UploadFile = File(...)):
    raise HTTPException(
        status_code=410,
        detail="Upload endpoint is disabled. Use WebSocket /ws/stream for streaming.",
    )


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    return _task_payload(task)


@app.get("/api/student/content")
async def get_student_content():
    return {
        "task_id": PUBLISHED_MATERIALS["task_id"],
        "summary": PUBLISHED_MATERIALS["summary"],
        "quiz_text": PUBLISHED_MATERIALS["quiz_text"],
        "analytics": PUBLISHED_MATERIALS["analytics"],
        "updated_at": PUBLISHED_MATERIALS["updated_at"],
    }


async def _check_ml_available() -> bool:
    try:
        async with websockets.connect(
            ML_WS_URL,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            init_payload = {"type": "init", "config": {"language": None}}
            await ws.send(json.dumps(init_payload))
            data = await asyncio.wait_for(ws.recv(), timeout=INIT_TIMEOUT_SEC)
            if isinstance(data, (bytes, bytearray)):
                return False
            try:
                msg = json.loads(data)
            except Exception:
                return False
            return msg.get("type") == "init_ack"
    except Exception:
        return False


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ml_available": await _check_ml_available(),
    }


@app.get("/check-ml")
async def check_ml():
    ok = await _check_ml_available()
    return {"ml_available": ok}


async def _broadcast_task(task_id: str) -> None:
    task = TASKS.get(task_id)
    if not task:
        return
    payload = _task_payload(task)
    conns = TASK_SUBSCRIBERS.get(task_id, set())
    if not conns:
        return
    dead: List[WebSocket] = []
    for ws in conns:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.discard(ws)


@app.websocket("/ws/tasks/{task_id}")
async def ws_task_updates(ws: WebSocket, task_id: str):
    await ws.accept()
    conns = TASK_SUBSCRIBERS.setdefault(task_id, set())
    conns.add(ws)
    try:
        if task_id in TASKS:
            await _broadcast_task(task_id)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        conns.discard(ws)


@app.websocket("/ws/stream")
async def ws_stream_proxy(client_ws: WebSocket):
    await client_ws.accept()
    logger.info("ws_stream: client connected")

    async def _safe_close(ws: WebSocket):
        try:
            if ws.application_state != WebSocketState.DISCONNECTED:
                await ws.close()
        except Exception:
            pass
    try:
        init_msg = await asyncio.wait_for(client_ws.receive_text(), timeout=INIT_TIMEOUT_SEC)
        try:
            init_payload = json.loads(init_msg)
        except Exception:
            await client_ws.send_json(_error_payload("INVALID_INIT", "Некорректный init payload.", 400))
            await _safe_close(client_ws)
            return
        if init_payload.get("type") != "init":
            await client_ws.send_json(_error_payload("INVALID_INIT", "Ожидалось сообщение type=init.", 400))
            await _safe_close(client_ws)
            return
    except asyncio.TimeoutError:
        await client_ws.send_json(_error_payload("TIMEOUT", "Истекло время ожидания init-сообщения.", 400))
        await _safe_close(client_ws)
        return

    task_id = str(uuid.uuid4())
    now = time.time()
    task = {
        "id": task_id,
        "status": "processing",
        "progress": 0,
        "filename": init_payload.get("filename", "stream-upload"),
        "content_type": init_payload.get("content_type", "application/octet-stream"),
        "created_at": now,
        "updated_at": now,
        "transcript": [],
        "summary": [],
        "test": None,
        "quiz_text": "",
        "analytics": None,
        "error": None,
        "error_code": None,
        "error_status_code": None,
    }
    TASKS[task_id] = task

    try:
        logger.info("ws_stream: connecting to ML %s", ML_WS_URL)
        ml_ws = await websockets.connect(
            ML_WS_URL,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
    except Exception:
        logger.exception("ws_stream: ML connect failed")
        task["status"] = "failed"
        task["error"] = "ML-сервис недоступен."
        task["error_code"] = "ML_UNAVAILABLE"
        task["error_status_code"] = 503
        task["updated_at"] = time.time()
        await _broadcast_task(task_id)
        await client_ws.send_json(_error_payload("ML_UNAVAILABLE", "ML-сервис недоступен.", 503))
        await _safe_close(client_ws)
        return

    try:
        logger.info("ws_stream: sending init to ML")
        await ml_ws.send(json.dumps(init_payload))
        ack = await asyncio.wait_for(ml_ws.recv(), timeout=INIT_TIMEOUT_SEC)
        if isinstance(ack, (bytes, bytearray)):
            logger.error("ws_stream: init ack from ML was binary")
            await client_ws.send_json(_error_payload("ML_DISCONNECTED", "ML-сервис вернул некорректный ответ.", 503))
            await _safe_close(client_ws)
            await ml_ws.close()
            return
        try:
            ack_msg = json.loads(ack)
        except Exception:
            logger.exception("ws_stream: failed to decode init ack")
            await client_ws.send_json(_error_payload("ML_DISCONNECTED", "ML-сервис вернул некорректный ответ.", 503))
            await _safe_close(client_ws)
            await ml_ws.close()
            return
        if ack_msg.get("type") != "init_ack":
            logger.error("ws_stream: init ack has unexpected type: %s", ack_msg)
            await client_ws.send_json(_error_payload("ML_DISCONNECTED", "ML-сервис не подтвердил инициализацию.", 503))
            await _safe_close(client_ws)
            await ml_ws.close()
            return
        logger.info("ws_stream: init_ack received")
        await client_ws.send_json({"type": "init_ack"})
    except asyncio.TimeoutError:
        logger.exception("ws_stream: init ack timeout")
        task["status"] = "failed"
        task["error"] = "ML-сервис не ответил вовремя."
        task["error_code"] = "TIMEOUT"
        task["error_status_code"] = 503
        task["updated_at"] = time.time()
        await _broadcast_task(task_id)
        await client_ws.send_json(_error_payload("TIMEOUT", "ML-сервис не ответил вовремя.", 503))
        await _safe_close(client_ws)
        await ml_ws.close()
        return
    except Exception:
        logger.exception("ws_stream: init/ack failed")
        task["status"] = "failed"
        task["error"] = "Не удалось инициализировать ML-сервис."
        task["error_code"] = "ML_DISCONNECTED"
        task["error_status_code"] = 503
        task["updated_at"] = time.time()
        await _broadcast_task(task_id)
        await client_ws.send_json(_error_payload("ML_DISCONNECTED", "Не удалось инициализировать ML-сервис.", 503))
        await _safe_close(client_ws)
        await ml_ws.close()
        return

    async def forward_client_to_ml():
        try:
            while True:
                msg = await client_ws.receive()
                if msg.get("bytes") is not None:
                    logger.info("ws_stream: forward bytes %d", len(msg["bytes"]))
                    await ml_ws.send(msg["bytes"])
                elif msg.get("text") is not None:
                    text = msg["text"]
                    if _is_json_command(text):
                        logger.info("ws_stream: forward text command %s", text)
                        await ml_ws.send(text)
                    else:
                        logger.warning("Invalid client text message ignored")
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    async def forward_ml_to_client():
        try:
            while True:
                data = await asyncio.wait_for(ml_ws.recv(), timeout=ML_WS_TIMEOUT_SEC)
                if isinstance(data, (bytes, bytearray)):
                    # ignore binary from ML service (if any)
                    continue
                logger.info("ws_stream: recv from ML %s", data[:200])
                try:
                    msg = json.loads(data)
                except Exception:
                    msg = None

                if isinstance(msg, dict):
                    task["updated_at"] = time.time()

                    if msg.get("type") == "summary":
                        task["summary"] = _summary_lines_from_text(msg.get("text", ""))
                        task["progress"] = max(task["progress"], 85)
                        _publish_task_materials(task)
                        await _broadcast_task(task_id)
                    elif msg.get("type") == "quiz_text":
                        task["quiz_text"] = msg.get("text", "")
                        task["progress"] = 100
                        task["status"] = "done"
                        _publish_task_materials(task)
                        await _broadcast_task(task_id)
                    elif msg.get("type") == "analytics":
                        task["analytics"] = msg.get("analytics")
                        task["progress"] = 100
                        task["status"] = "done"
                        _publish_task_materials(task)
                        await _broadcast_task(task_id)
                    elif msg.get("type") == "error" or msg.get("error"):
                        task["status"] = "failed"
                        task["error_code"] = msg.get("code") or "ML_ERROR"
                        task["error_status_code"] = int(msg.get("status_code") or 500)
                        task["error"] = msg.get("detail") or msg.get("code") or msg.get("error")
                        logger.error(
                            "ws_stream: ML error code=%s status=%s detail=%s",
                            task["error_code"],
                            task["error_status_code"],
                            task["error"],
                        )
                        await _broadcast_task(task_id)
                    elif msg.get("text") and not msg.get("text", "").startswith("[partial]") and msg.get("is_final"):
                        task["transcript"].append({
                            "start": int((msg.get("start") or 0) / 1000),
                            "text": msg.get("text", ""),
                        })
                        task["progress"] = max(task["progress"], 50)
                        await _broadcast_task(task_id)

                await client_ws.send_text(data)
        except asyncio.TimeoutError:
            task["status"] = "failed"
            task["error"] = "ML-сервис не ответил вовремя."
            task["error_code"] = "TIMEOUT"
            task["error_status_code"] = 503
            task["updated_at"] = time.time()
            logger.exception("ws_stream: timeout while waiting ML response")
            await _broadcast_task(task_id)
            await client_ws.send_json(_error_payload("TIMEOUT", "ML-сервис не ответил вовремя.", 503))
        except Exception:
            if task["status"] == "processing":
                task["status"] = "failed"
                task["error"] = "Потеряно соединение с ML-сервисом."
                task["error_code"] = "ML_DISCONNECTED"
                task["error_status_code"] = 503
                task["updated_at"] = time.time()
                logger.exception("ws_stream: ML disconnected unexpectedly")
                await _broadcast_task(task_id)
            await client_ws.send_json(_error_payload("ML_DISCONNECTED", "Потеряно соединение с ML-сервисом.", 503))

    task1 = asyncio.create_task(forward_client_to_ml())
    task2 = asyncio.create_task(forward_ml_to_client())
    done, pending = await asyncio.wait(
        {task1, task2},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    await ml_ws.close()
    await _safe_close(client_ws)
