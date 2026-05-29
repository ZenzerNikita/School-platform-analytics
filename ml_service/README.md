# ML Transcription Service

Runs a WebSocket endpoint for streaming audio chunks and returning partial/final transcripts.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001
```

## Env

- `WHISPER_MODEL_SIZE` (default: `tiny`)
- `WHISPER_DEVICE` (default: `cpu`)
- `WHISPER_COMPUTE_TYPE` (default: `int8`)
- `WHISPER_BEAM_SIZE` (default: `1`)
- `SUMMARY_PROVIDER` (default: `groq`, optional: `heuristic`)
- `SUMMARY_MODEL_NAME` (default: `llama-3.3-70b-versatile`)
- `QUIZ_PROVIDER` (default: `groq`, optional: `heuristic`)
- `QUIZ_MODEL_NAME` (default: `llama-3.3-70b-versatile`)
- `ANALYTICS_PROVIDER` (default: `groq`, optional: `heuristic`)
- `GROQ_ANALYTICS_MODEL` (default: `qwen/qwen3-32b`)
- `PRELOAD_MODELS_ON_STARTUP` (default: `true`)
- `GROQ_API_URL` (default: `https://api.groq.com/openai/v1/chat/completions`)
- `GROQ_API_KEY` or `API_KEY_STORAGE`

## WebSocket

- URL: `ws://<host>:8001/transcriber/ws/transcribe`
- Send audio as binary chunks (WAV bytes recommended)
- Send JSON init first: `{"type":"init","config":{"language":null}}`
- Send JSON command `{"type":"end"}` to finish
- Server sends JSON messages: `{ text, start, is_final }`

`start` is in milliseconds.
