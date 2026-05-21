import argparse
import os
from pathlib import Path
from faster_whisper import WhisperModel


def resolve_whisper_model_source(model_name):
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


def transcribe_audio(audio_path, whisper_model_name, whisper_compute_type, output_path):
    text, _segments = transcribe_audio_to_text(
        audio_path, whisper_model_name, whisper_compute_type
    )

    return text


def transcribe_audio_to_text(audio_path, whisper_model_name, whisper_compute_type):
    whisper_model = WhisperModel(
        resolve_whisper_model_source(whisper_model_name),
        device="cpu",
        compute_type=whisper_compute_type,
    )

    segments, _info = whisper_model.transcribe(
        audio_path,
        language="ru",
        beam_size=5,
        vad_filter=True,
    )

    text = "".join(seg.text for seg in segments).strip()
    segment_items = [
        {"start": float(seg.start), "text": seg.text.strip()} for seg in segments
    ]
    return text, segment_items

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

    print(text)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", default="audio.mp3")
    parser.add_argument("--output", default="transcript.txt")
    parser.add_argument("--whisper-model", default="tiny")
    parser.add_argument("--whisper-compute-type", default="int8")
    return parser.parse_args()


def main():
    args = parse_args()
    transcribe_audio(
        args.audio,
        args.whisper_model,
        args.whisper_compute_type,
        args.output,
    )


if __name__ == "__main__":
    main()
