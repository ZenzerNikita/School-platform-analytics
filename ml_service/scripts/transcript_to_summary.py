import argparse
import logging
import os
import re
import sys

from scripts.groq_client import chat_completion

logger = logging.getLogger("ml_service.summary")
DEFAULT_SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _normalize_whitespace(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def _split_sentences(text):
    normalized = _normalize_whitespace(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _dedupe_sentences(sentences):
    seen = set()
    result = []
    for sentence in sentences:
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(sentence)
    return result


def heuristic_summarize_text(text, max_items=6):
    sentences = _dedupe_sentences(_split_sentences(text))
    if not sentences:
        raise ValueError("Текст пустой")

    intro = sentences[0]
    body = []
    for sentence in sentences[1:]:
        cleaned = sentence.strip(" -")
        if len(cleaned) < 12:
            continue
        body.append(cleaned)
        if len(body) >= max_items:
            break

    summary_lines = ["## Краткий конспект", intro]
    if body:
        summary_lines.append("## Основные пункты")
        summary_lines.extend(f"- {item}" for item in body)

    return "\n".join(summary_lines).strip()


def _build_summary_prompt(text: str) -> str:
    transcript = text.strip()[:18000]
    return (
        "Ниже дана транскрибация занятия на русском языке.\n"
        "Сделай по ней структурированный конспект.\n"
        "Верни только готовый текст конспекта без пояснений.\n\n"
        "Требования:\n"
        "- исправь очевидные ошибки распознавания речи по смыслу\n"
        "- убери повторы, шум устной речи и рекламные вставки\n"
        "- выдели ключевые определения, шаги решения и итоговые ответы\n"
        "- используй markdown-заголовки и маркированные списки\n"
        "- сделай 4-6 крупных смысловых разделов, а не много мелких однотипных пунктов\n"
        "- внутри каждого раздела давай 2-5 содержательных bullet points, когда это уместно\n"
        "- не делай отдельный заголовок на каждую одну строку или микрофакт\n"
        "- если это разбор задачи, опиши решение пошагово\n\n"
        "Транскрибация:\n"
        f"{transcript}"
    )


def summarize_text(
    text,
    model_name=DEFAULT_SUMMARY_MODEL_NAME,
):
    if not text:
        raise ValueError("Текст пустой")

    provider = os.getenv("SUMMARY_PROVIDER", "groq").strip().lower()
    logger.info("summary provider=%s chars=%s", provider, len(text))
    if provider == "heuristic":
        return heuristic_summarize_text(text)
    if provider not in {"groq", "llm"}:
        raise ValueError(f"Unsupported summary provider: {provider}")

    return chat_completion(
        system_prompt="Ты готовишь аккуратный конспект урока по транскрибации.",
        user_prompt=_build_summary_prompt(text),
        model=model_name,
        max_completion_tokens=int(os.getenv("SUMMARY_MAX_TOKENS", "700")),
        temperature=float(os.getenv("SUMMARY_TEMPERATURE", "0.1")),
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="transcript.txt")
    parser.add_argument("--output", default="summary.txt")
    parser.add_argument("--model", default=DEFAULT_SUMMARY_MODEL_NAME)
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        text = read_text(args.input)
        if not text:
            raise ValueError("Файл с транскриптом пустой")

        final_summary = summarize_text(
            text,
            model_name=args.model,
        )

        write_text(args.output, final_summary)

    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
