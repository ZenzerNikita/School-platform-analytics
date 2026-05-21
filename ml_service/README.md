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

- `WHISPER_MODEL_SIZE` (default: `small`)
- `WHISPER_DEVICE` (default: `cpu`)
- `WHISPER_COMPUTE_TYPE` (default: `int8`)

## WebSocket

- URL: `ws://<host>:8001/transcriber/ws/transcribe`
- Send audio as binary chunks (WAV bytes recommended)
- Send JSON init first: `{"type":"init","config":{"language":null}}`
- Send JSON command `{"type":"end"}` to finish
- Server sends JSON messages: `{ text, start, is_final }`

`start` is in milliseconds.
