# Adaptive Learning Platform

Платформа состоит из двух сервисов:

- `app` — teacher/student UI и прокси-API
- `ml_service` — распознавание аудио, генерация конспекта, теста и отдельный async-анализ транскрипта

## Run

Требования:

- Docker Desktop
- Docker Compose

Локальная настройка:

```bash
cp .env.example .env
```

Запуск:

```bash
docker compose up --build
```

Быстрые URL:

- teacher UI: `http://localhost:8000/teacher`
- student UI: `http://localhost:8000/student`
- ML service health: `http://localhost:8001/analytics/health`

Если образы уже собраны и код примонтирован volume'ами, обычно достаточно:

```bash
docker compose up -d --force-recreate app ml_service
```

## Env

Минимально нужен:

- `GROQ_API_KEY` — ключ для summary, quiz и LLM-аналитики

Полезные переменные:

- `WHISPER_MODEL_SIZE` — размер Whisper, по умолчанию `tiny`
- `WHISPER_BEAM_SIZE` — beam size для транскрибации, по умолчанию `1`
- `SUMMARY_PROVIDER` — `groq` или `heuristic`
- `QUIZ_PROVIDER` — `groq` или `heuristic`
- `ANALYTICS_PROVIDER` — `groq` или `heuristic`

## Main Flow

Основной teacher pipeline теперь такой:

1. teacher загружает аудио/видео
2. `ml_service` делает транскрипт
3. `ml_service` генерирует summary
4. `ml_service` генерирует quiz
5. analytics не считается внутри этого пайплайна
6. analytics запускается отдельным запросом по готовому транскрипту

## Analytics API Contract

### Direct ML endpoint

Создать задачу анализа:

`POST /transcript/analyze/`

Request:

```json
{
  "transcript": [
    {
      "start_ms": 0,
      "end_ms": 1200,
      "text": "Сегодня мы разбираем квадратные уравнения."
    }
  ]
}
```

Допустим и упрощённый вариант:

```json
{
  "transcript": "Сегодня мы разбираем квадратные уравнения."
}
```

Response `202 Accepted`:

```json
{
  "job_id": "fd2d4cda-c5e4-4bf1-a219-ca92667af640",
  "status": "queued",
  "created_at": 1780147036.2648249,
  "updated_at": 1780147036.2648249,
  "poll_url": "/transcript/analyze/fd2d4cda-c5e4-4bf1-a219-ca92667af640"
}
```

Проверить статус:

`GET /transcript/analyze/{job_id}`

Response while running:

```json
{
  "job_id": "fd2d4cda-c5e4-4bf1-a219-ca92667af640",
  "status": "processing",
  "created_at": 1780147036.2648249,
  "updated_at": 1780147037.001
}
```

Response when completed:

```json
{
  "job_id": "fd2d4cda-c5e4-4bf1-a219-ca92667af640",
  "status": "completed",
  "created_at": 1780147036.2648249,
  "updated_at": 1780147039.8493257,
  "result": {
    "type": "lesson_analytics",
    "source": "groq",
    "total_score": 5,
    "max_score": 14,
    "metrics": []
  }
}
```

Response when failed:

```json
{
  "job_id": "fd2d4cda-c5e4-4bf1-a219-ca92667af640",
  "status": "failed",
  "created_at": 1780147036.2648249,
  "updated_at": 1780147038.1001,
  "error": {
    "code": "ANALYTICS_FAILED",
    "detail": "Не удалось сформировать аналитику занятия."
  }
}
```

`curl` пример:

```bash
curl -X POST http://localhost:8001/transcript/analyze/ \
  -H 'Content-Type: application/json' \
  -d '{
    "transcript": [
      {
        "start_ms": 0,
        "end_ms": 1200,
        "text": "Сегодня мы разбираем квадратные уравнения."
      }
    ]
  }'
```

```bash
curl http://localhost:8001/transcript/analyze/<job_id>
```

### App proxy endpoints

Для уже загруженного урока `app` поднимает прокси-слой над `ml_service`.

Создать analytics job по `task_id`:

`POST /api/tasks/{task_id}/analytics`

Response `202 Accepted`:

```json
{
  "job_id": "2dc0f3c1-117e-44ab-8c2f-c4adb8a01b9f",
  "status": "queued",
  "poll_url": "/api/tasks/{task_id}/analytics/{job_id}",
  "task": {
    "id": "bf6268a8-df0b-42af-93fb-73f713349deb",
    "status": "done",
    "analytics_job_id": "2dc0f3c1-117e-44ab-8c2f-c4adb8a01b9f",
    "analytics_job_status": "queued"
  }
}
```

Проверить job:

`GET /api/tasks/{task_id}/analytics/{job_id}`

Response:

```json
{
  "job_id": "2dc0f3c1-117e-44ab-8c2f-c4adb8a01b9f",
  "status": "completed",
  "result": {
    "type": "lesson_analytics",
    "source": "groq",
    "total_score": 8,
    "max_score": 14,
    "metrics": []
  },
  "error": null,
  "task": {
    "id": "bf6268a8-df0b-42af-93fb-73f713349deb",
    "status": "done",
    "analytics_job_status": "completed"
  }
}
```

`curl` пример:

```bash
curl -X POST http://localhost:8000/api/tasks/<task_id>/analytics
```

```bash
curl http://localhost:8000/api/tasks/<task_id>/analytics/<job_id>
```

## Notes

- `app` хранит опубликованные teacher-материалы и задачи в `app_runtime_data` volume
- `ml_service` хранит Hugging Face cache в `whisper_hf_cache` volume
- `ml_service` хранит analytics jobs в `ml_runtime_data` volume
- analytics вынесен из websocket pipeline и запускается отдельным запросом
- teacher UI теперь вызывает analytics вручную из вкладки `Аналитика`
- после рестарта `ml_service` завершённые jobs остаются доступны по `job_id`
- незавершённые jobs после рестарта помечаются как `failed` с кодом `JOB_INTERRUPTED`
- sync endpoint `POST /analytics/analyze` удалён, официальный контракт только через `POST /transcript/analyze/` + polling
