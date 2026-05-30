import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib import error as urllib_error
from urllib import request as urllib_request

import websockets
from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from fastapi.responses import FileResponse, JSONResponse
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
    "test": None,
    "quiz_text": "",
    "analytics": None,
    "updated_at": None,
}
APP_STORAGE_DIR = Path(os.getenv("APP_STORAGE_DIR", "/app/runtime"))
TASKS_STATE_PATH = APP_STORAGE_DIR / "tasks_state.json"
PUBLISHED_STATE_PATH = APP_STORAGE_DIR / "published_materials.json"

CHUNK_SIZE = 5
CHUNK_INTERVAL_SEC = 1

ML_WS_URL = os.getenv("ML_WS_URL", "ws://127.0.0.1:8001/transcriber/ws/transcribe")
ML_API_BASE_URL = os.getenv("ML_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
ML_WS_TIMEOUT_SEC = float(os.getenv("ML_WS_TIMEOUT_SEC", "300"))
INIT_TIMEOUT_SEC = float(os.getenv("INIT_TIMEOUT_SEC", "30"))
ML_HTTP_TIMEOUT_SEC = float(os.getenv("ML_HTTP_TIMEOUT_SEC", "60"))

logger = logging.getLogger("ws_proxy")
logging.basicConfig(level=logging.INFO)

QUIZ_QUESTION_RE = re.compile(r"^\s*Вопрос\s*\d+\s*:\s*(.+?)\s*$", re.IGNORECASE)
QUIZ_OPTION_RE = re.compile(r"^\s*([A-DА-Г])[\)\.\:]?\s*(.+?)\s*$", re.IGNORECASE)
QUIZ_CORRECT_RE = re.compile(r"^\s*Правильный\s+ответ\s*:\s*(.+?)\s*$", re.IGNORECASE)
QUIZ_OPTION_INDEX = {
    "A": 0,
    "B": 1,
    "C": 2,
    "D": 3,
    "А": 0,
    "Б": 1,
    "В": 2,
    "Г": 3,
}


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


def _parse_correct_answer(raw_value: str, options: List[str]) -> Optional[int]:
    candidate = (raw_value or "").strip()
    if not candidate:
        return None

    marker = candidate[0].upper()
    if marker in QUIZ_OPTION_INDEX:
        index = QUIZ_OPTION_INDEX[marker]
        if index < len(options):
            return index

    normalized = candidate.lower().strip(" .)")
    for index, option in enumerate(options):
        if normalized == option.lower().strip(" .)"):
            return index
    return None


def _finalize_test_question(
    questions: List[dict],
    question_text: str,
    options: List[str],
    correct_raw: str,
) -> None:
    if not question_text or len(options) < 2:
        return

    correct_answer = _parse_correct_answer(correct_raw, options)
    if correct_answer is None:
        return

    questions.append({
        "id": len(questions) + 1,
        "question": question_text.strip(),
        "options": options[:4],
        "correct_answer": correct_answer,
    })


def _parse_quiz_text(quiz_text: str) -> Optional[dict]:
    if not quiz_text or not quiz_text.strip():
        return None

    questions: List[dict] = []
    current_question = ""
    current_options: List[str] = []
    current_correct = ""

    for raw_line in quiz_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        question_match = QUIZ_QUESTION_RE.match(line)
        if question_match:
            _finalize_test_question(questions, current_question, current_options, current_correct)
            current_question = question_match.group(1).strip()
            current_options = []
            current_correct = ""
            continue

        option_match = QUIZ_OPTION_RE.match(line)
        if option_match and current_question:
            current_options.append(option_match.group(2).strip())
            continue

        correct_match = QUIZ_CORRECT_RE.match(line)
        if correct_match and current_question:
            current_correct = correct_match.group(1).strip()

    _finalize_test_question(questions, current_question, current_options, current_correct)

    if not questions:
        return None

    return {
        "title": "Тест по занятию",
        "questions": questions,
    }


def _ensure_task_test(task: dict) -> None:
    if task.get("test") or not task.get("quiz_text"):
        return
    task["test"] = _parse_quiz_text(task.get("quiz_text", ""))


def _task_payload(task: dict) -> dict:
    _ensure_task_test(task)
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
        "analytics_job_id": task.get("analytics_job_id"),
        "analytics_job_status": task.get("analytics_job_status"),
        "analytics_job_error": task.get("analytics_job_error"),
        "error": task.get("error"),
        "error_code": task.get("error_code"),
        "error_status_code": task.get("error_status_code"),
    }


def _persist_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _persist_state() -> None:
    serializable_tasks = {
        task_id: {
            key: value
            for key, value in task.items()
            if not key.startswith("_")
        }
        for task_id, task in TASKS.items()
    }
    _persist_json(TASKS_STATE_PATH, serializable_tasks)
    _persist_json(PUBLISHED_STATE_PATH, PUBLISHED_MATERIALS)


def _load_json(path: Path) -> Optional[object]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to load persisted json from %s", path)
        return None


def _restore_state() -> None:
    stored_tasks = _load_json(TASKS_STATE_PATH)
    if isinstance(stored_tasks, dict):
        TASKS.clear()
        for task_id, raw_task in stored_tasks.items():
            if isinstance(raw_task, dict):
                TASKS[str(task_id)] = raw_task

    stored_published = _load_json(PUBLISHED_STATE_PATH)
    if isinstance(stored_published, dict):
        PUBLISHED_MATERIALS.update({
            "task_id": stored_published.get("task_id"),
            "summary": list(stored_published.get("summary") or []),
            "test": stored_published.get("test"),
            "quiz_text": stored_published.get("quiz_text", ""),
            "analytics": stored_published.get("analytics"),
            "updated_at": stored_published.get("updated_at"),
        })
        return

    done_tasks = [task for task in TASKS.values() if isinstance(task, dict) and task.get("status") == "done"]
    if not done_tasks:
        return
    latest_task = max(done_tasks, key=lambda task: float(task.get("updated_at") or 0))
    _publish_task_materials(latest_task)


@app.on_event("startup")
async def restore_app_state_on_startup():
    _restore_state()


def _publish_task_materials(task: dict) -> None:
    if not task["summary"] and not task.get("quiz_text"):
        return

    _ensure_task_test(task)
    PUBLISHED_MATERIALS["task_id"] = task["id"]
    PUBLISHED_MATERIALS["summary"] = list(task["summary"])
    PUBLISHED_MATERIALS["test"] = task.get("test")
    PUBLISHED_MATERIALS["quiz_text"] = task.get("quiz_text", "")
    PUBLISHED_MATERIALS["analytics"] = task.get("analytics")
    PUBLISHED_MATERIALS["updated_at"] = task["updated_at"]
    _persist_state()


def _ml_http_json_request_sync(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    timeout_sec: float = ML_HTTP_TIMEOUT_SEC,
) -> tuple[int, dict]:
    request_body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib_request.Request(
        f"{ML_API_BASE_URL}{path}",
        data=request_body,
        method=method,
        headers=headers,
    )

    try:
        with urllib_request.urlopen(request, timeout=timeout_sec) as response:
            raw_body = response.read().decode("utf-8")
            status_code = int(response.status)
    except urllib_error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        status_code = int(exc.code)
    except urllib_error.URLError as exc:
        raise RuntimeError(str(exc.reason) or "ML API unavailable") from exc

    try:
        decoded = json.loads(raw_body) if raw_body else {}
    except Exception:
        decoded = {}
    return status_code, decoded if isinstance(decoded, dict) else {}


async def _ml_http_json_request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    timeout_sec: float = ML_HTTP_TIMEOUT_SEC,
) -> tuple[int, dict]:
    return await asyncio.to_thread(
        _ml_http_json_request_sync,
        method,
        path,
        payload,
        timeout_sec,
    )


def _student_payload(task: dict) -> dict:
    _ensure_task_test(task)
    return {
        "task_id": task["id"],
        "summary": list(task.get("summary") or []),
        "test": task.get("test"),
        "quiz_text": task.get("quiz_text", ""),
        "analytics": task.get("analytics"),
        "updated_at": task.get("updated_at"),
        "status": task.get("status"),
    }


def _summary_lines_from_text(text: str) -> List[str]:
    normalized_text = (text or "").replace("\r\n", "\n").strip()
    if not normalized_text:
        return []

    lines = normalized_text.split("\n")
    has_headings = any(line.strip().startswith("#") for line in lines if line.strip())
    if not has_headings:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized_text) if block.strip()]
        return blocks or [normalized_text]

    sections: List[str] = []
    current: List[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            if current and current[-1] != "":
                current.append("")
            continue
        if stripped.startswith("#"):
            if current:
                section = "\n".join(current).strip()
                if section:
                    sections.append(section)
            current = [stripped]
            continue
        if not current:
            current = ["## Краткий конспект"]
        current.append(stripped)

    if current:
        section = "\n".join(current).strip()
        if section:
            sections.append(section)

    if len(sections) <= 5:
        return sections

    parsed_sections = []
    for index, section in enumerate(sections, start=1):
        section_lines = [line for line in section.split("\n") if line.strip()]
        if not section_lines:
            continue

        first_line = section_lines[0].strip()
        heading_match = re.match(r"^#{1,6}\s+(.+)$", first_line)
        if heading_match:
            title = heading_match.group(1).strip()
            body = "\n".join(line.strip() for line in section_lines[1:]).strip()
        else:
            title = f"Раздел {index}"
            body = "\n".join(line.strip() for line in section_lines).strip()

        parsed_sections.append({
            "title": title,
            "body": body or title,
        })

    if len(parsed_sections) <= 5:
        return sections

    max_sections = 5
    base_group_size = len(parsed_sections) // max_sections
    extra = len(parsed_sections) % max_sections
    merged_sections: List[str] = []
    offset = 0

    for group_index in range(max_sections):
        group_size = base_group_size + (1 if group_index < extra else 0)
        group = parsed_sections[offset:offset + group_size]
        offset += group_size
        if not group:
            continue

        title = group[0]["title"]
        body_parts = [group[0]["body"]]
        for item in group[1:]:
            body_parts.append(f"- **{item['title']}**\n{item['body']}")

        merged_body = "\n\n".join(part.strip() for part in body_parts if part.strip()).strip()
        merged_sections.append(f"## {title}\n{merged_body}".strip())

    return merged_sections


def _test_to_quiz_text(test: dict) -> str:
    questions = test.get("questions") or []
    parts: List[str] = []
    for index, question in enumerate(questions, start=1):
        options = list(question.get("options") or [])
        correct_index = int(question.get("correct_answer") or 0)
        parts.append(f"Вопрос {index}: {question.get('question', '').strip()}")
        for option_index, option in enumerate(options):
            parts.append(f"{chr(65 + option_index)}) {str(option).strip()}")
        if 0 <= correct_index < len(options):
            parts.append(f"Правильный ответ: {chr(65 + correct_index)}")
        parts.append("")
    return "\n".join(parts).strip()


def _normalize_test_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be an object.")

    title = str(payload.get("title") or "Тест по занятию").strip()
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("questions must be a non-empty list.")

    questions = []
    for index, raw_question in enumerate(raw_questions, start=1):
        if not isinstance(raw_question, dict):
            continue

        question_text = str(raw_question.get("question") or "").strip()
        raw_options = raw_question.get("options")
        if not question_text or not isinstance(raw_options, list):
            continue

        options = [str(option).strip() for option in raw_options if str(option).strip()]
        if len(options) < 2:
            continue
        options = options[:4]

        try:
            correct_answer = int(raw_question.get("correct_answer", 0))
        except (TypeError, ValueError):
            correct_answer = 0
        correct_answer = max(0, min(correct_answer, len(options) - 1))

        questions.append({
            "id": index,
            "question": question_text,
            "options": options,
            "correct_answer": correct_answer,
        })

    if not questions:
        raise ValueError("No valid questions in test payload.")

    return {
        "title": title,
        "questions": questions,
    }


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
@app.get("/teacher/")
@app.get("/teacher/index.html")
async def teacher_index():
    return FileResponse("app/static/index.html")


@app.get("/student")
@app.get("/student/")
@app.get("/student/index.html")
async def student_index():
    return FileResponse("app/static/student/index.html")


@app.get("/material/{task_id}")
@app.get("/material/{task_id}/")
async def material_page(task_id: str):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
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


@app.patch("/api/tasks/{task_id}/test")
async def update_task_test(task_id: str, payload: dict = Body(...)):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    try:
        normalized_test = _normalize_test_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task["test"] = normalized_test
    task["quiz_text"] = _test_to_quiz_text(normalized_test)
    task["updated_at"] = time.time()
    _publish_task_materials(task)
    await _broadcast_task(task_id)
    return _task_payload(task)


@app.get("/api/student/content")
async def get_student_content():
    return {
        "task_id": PUBLISHED_MATERIALS["task_id"],
        "summary": PUBLISHED_MATERIALS["summary"],
        "test": PUBLISHED_MATERIALS["test"],
        "quiz_text": PUBLISHED_MATERIALS["quiz_text"],
        "analytics": PUBLISHED_MATERIALS["analytics"],
        "updated_at": PUBLISHED_MATERIALS["updated_at"],
    }


@app.get("/api/student/{task_id}")
async def get_student_material(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("status") != "done":
        raise HTTPException(status_code=409, detail="material is not ready yet")
    return _student_payload(task)


@app.post("/api/tasks/{task_id}/analytics")
async def request_task_analytics(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if not task.get("transcript"):
        raise HTTPException(status_code=409, detail="transcript is not ready yet")

    if task.get("analytics_job_id") and task.get("analytics_job_status") in {"queued", "processing"}:
        return JSONResponse(
            status_code=202,
            content={
                "job_id": task.get("analytics_job_id"),
                "status": task.get("analytics_job_status"),
                "task": _task_payload(task),
                "poll_url": f"/api/tasks/{task_id}/analytics/{task.get('analytics_job_id')}",
            },
        )

    try:
        status_code, response_payload = await _ml_http_json_request(
            "POST",
            "/transcript/analyze/",
            {"transcript": task.get("transcript", [])},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"analytics service unavailable: {exc}") from exc

    if status_code != 202:
        detail = response_payload.get("detail") or response_payload.get("error") or "analytics request failed"
        raise HTTPException(status_code=status_code or 500, detail=detail)

    task["analytics_job_id"] = response_payload.get("job_id")
    task["analytics_job_status"] = response_payload.get("status", "queued")
    task["analytics_job_error"] = None
    task["updated_at"] = time.time()
    _persist_state()
    await _broadcast_task(task_id)

    return JSONResponse(
        status_code=202,
        content={
            "job_id": task["analytics_job_id"],
            "status": task["analytics_job_status"],
            "task": _task_payload(task),
            "poll_url": f"/api/tasks/{task_id}/analytics/{task['analytics_job_id']}",
        },
    )


@app.get("/api/tasks/{task_id}/analytics/{job_id}")
async def poll_task_analytics(task_id: str, job_id: str):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("analytics_job_id") and task.get("analytics_job_id") != job_id:
        raise HTTPException(status_code=409, detail="analytics job mismatch")

    try:
        status_code, response_payload = await _ml_http_json_request(
            "GET",
            f"/transcript/analyze/{job_id}",
            None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"analytics service unavailable: {exc}") from exc

    if status_code == 404:
        task["analytics_job_id"] = job_id
        task["analytics_job_status"] = "failed"
        task["analytics_job_error"] = {
            "code": "JOB_NOT_FOUND",
            "detail": "Задача анализа не найдена.",
        }
        task["updated_at"] = time.time()
        _persist_state()
        await _broadcast_task(task_id)
        raise HTTPException(status_code=404, detail="analytics job not found")

    if status_code >= 400:
        detail = response_payload.get("detail") or response_payload.get("error") or "analytics poll failed"
        raise HTTPException(status_code=status_code, detail=detail)

    task["analytics_job_id"] = response_payload.get("job_id", job_id)
    task["analytics_job_status"] = response_payload.get("status")
    task["analytics_job_error"] = response_payload.get("error")
    if response_payload.get("status") == "completed":
        task["analytics"] = response_payload.get("result")
        task["updated_at"] = time.time()
        _publish_task_materials(task)
    else:
        task["updated_at"] = time.time()
        _persist_state()
    await _broadcast_task(task_id)

    return {
        "job_id": task["analytics_job_id"],
        "status": task["analytics_job_status"],
        "result": task.get("analytics") if task.get("analytics_job_status") == "completed" else None,
        "error": task.get("analytics_job_error"),
        "task": _task_payload(task),
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


@app.websocket("/ws/generations")
async def ws_generations_compat(ws: WebSocket):
    # Legacy compatibility endpoint for older cached frontends.
    # Keep the socket open and respond to pings so stale clients do not spam 403s.
    await ws.accept()
    try:
        while True:
            message = await ws.receive()
            if message.get("text") is not None:
                text = message["text"]
                if _is_json_command(text):
                    try:
                        payload = json.loads(text)
                    except Exception:
                        payload = {}
                    if payload.get("type") == "ping":
                        await ws.send_json({"type": "pong"})
            elif message.get("bytes") is not None:
                continue
    except WebSocketDisconnect:
        pass


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
        "analytics_job_id": None,
        "analytics_job_status": None,
        "analytics_job_error": None,
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
        await client_ws.send_json({"type": "init_ack", "task_id": task_id})
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
                        msg["summary"] = task["summary"]
                        _publish_task_materials(task)
                        await _broadcast_task(task_id)
                    elif msg.get("type") == "quiz_text":
                        task["quiz_text"] = msg.get("text", "")
                        task["test"] = _parse_quiz_text(task["quiz_text"])
                        task["progress"] = 100
                        task["status"] = "done"
                        msg["test"] = task.get("test")
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

                if isinstance(msg, dict):
                    await client_ws.send_text(json.dumps(msg, ensure_ascii=False))
                else:
                    await client_ws.send_text(data)
        except websockets.exceptions.ConnectionClosedOK:
            logger.info("ws_stream: ML connection closed normally")
            return
        except websockets.exceptions.ConnectionClosedError:
            if task["status"] == "processing":
                task["status"] = "failed"
                task["error"] = "Потеряно соединение с ML-сервисом."
                task["error_code"] = "ML_DISCONNECTED"
                task["error_status_code"] = 503
                task["updated_at"] = time.time()
                logger.exception("ws_stream: ML disconnected unexpectedly")
                await _broadcast_task(task_id)
                await client_ws.send_json(_error_payload("ML_DISCONNECTED", "Потеряно соединение с ML-сервисом.", 503))
            else:
                logger.info("ws_stream: ML connection closed after successful completion")
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
            else:
                logger.info("ws_stream: ignoring late ML disconnect after completed task")

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
