import argparse
import logging
import os
import sys

from scripts.groq_client import chat_completion

logger = logging.getLogger("ml_service.quiz")
DEFAULT_QUIZ_MODEL_NAME = os.getenv("QUIZ_MODEL_NAME", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _summary_lines(summary_text):
    return [
        line.strip("•- ").strip()
        for line in (summary_text or "").splitlines()
        if line.strip() and not line.strip().startswith("##")
    ]


def heuristic_generate_quiz(summary_text):
    facts = _summary_lines(summary_text)
    if not facts:
        raise ValueError("Конспект пустой")

    picked = facts[:3]
    blocks = []
    for index, fact in enumerate(picked, start=1):
        distractor_base = facts[(index) % len(facts)] if len(facts) > 1 else "Материал не разбирался"
        options = [
            fact,
            distractor_base,
            "Это не упоминалось в занятии",
            "Точного ответа в конспекте нет",
        ]
        blocks.append(
            "\n".join([
                f"Вопрос {index}: Что верно по материалам занятия?",
                f"A) {options[0]}",
                f"B) {options[1]}",
                f"C) {options[2]}",
                f"D) {options[3]}",
                "Правильный ответ: A",
            ])
        )
    return "\n\n".join(blocks).strip()


def _build_quiz_prompt(summary_text: str) -> str:
    summary = summary_text.strip()[:14000]
    return (
        "Ниже дан конспект занятия на русском языке.\n"
        "Составь по нему небольшой тест для ученика.\n"
        "Верни только чистый текст без JSON и без markdown-обрамления.\n\n"
        "Формат строго такой:\n"
        "Вопрос 1: ...\n"
        "A) ...\n"
        "B) ...\n"
        "C) ...\n"
        "D) ...\n"
        "Правильный ответ: A\n\n"
        "Вопрос 2: ...\n"
        "A) ...\n"
        "B) ...\n"
        "C) ...\n"
        "D) ...\n"
        "Правильный ответ: B\n\n"
        "Требования:\n"
        "- сделай 3-5 вопросов\n"
        "- вопросы должны проверять понимание темы, а не формальные мелочи\n"
        "- неправильные варианты должны быть правдоподобными\n"
        "- не добавляй пояснений до или после теста\n\n"
        "Конспект:\n"
        f"{summary}"
    )


def generate_quiz(
    summary_text,
    model_name=DEFAULT_QUIZ_MODEL_NAME,
):
    if not summary_text:
        raise ValueError("Конспект пустой")

    provider = os.getenv("QUIZ_PROVIDER", "groq").strip().lower()
    logger.info("quiz provider=%s chars=%s", provider, len(summary_text))
    if provider == "heuristic":
        return heuristic_generate_quiz(summary_text)
    if provider not in {"groq", "llm"}:
        raise ValueError(f"Unsupported quiz provider: {provider}")

    return chat_completion(
        system_prompt="Ты создаешь короткие качественные учебные тесты по конспекту урока.",
        user_prompt=_build_quiz_prompt(summary_text),
        model=model_name,
        max_completion_tokens=int(os.getenv("QUIZ_MAX_TOKENS", "900")),
        temperature=float(os.getenv("QUIZ_TEMPERATURE", "0.2")),
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="summary.txt")
    parser.add_argument("--output", default="quiz.txt")
    parser.add_argument("--model", default=DEFAULT_QUIZ_MODEL_NAME)
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        summary_text = read_text(args.input)
        if not summary_text:
            raise ValueError("Файл с конспектом пустой")

        quiz = generate_quiz(
            summary_text,
            model_name=args.model,
        )
        write_text(args.output, quiz)
        print(quiz)

    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
