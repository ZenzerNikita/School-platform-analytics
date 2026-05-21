import argparse
import json
import re
import sys
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def extract_json(text):
    text = text.strip()

    try:
        return json.loads(text)
    except:
        pass

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError("Модель не вернула JSON")

    candidate = match.group(0)

    try:
        return json.loads(candidate)
    except Exception as e:
        raise ValueError(f"Не удалось распарсить JSON: {e}")


def validate_quiz(data):
    if isinstance(data, list):
        data = {"questions": data}

    if not isinstance(data, dict):
        raise ValueError("Корневой JSON должен быть объектом или списком")

    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        raise ValueError("В JSON нет списка questions")

    normalized = []

    for i, item in enumerate(questions, 1):
        if not isinstance(item, dict):
            continue

        question = item.get("question")
        options = item.get("options")
        correct_answer = item.get("correct_answer")
        explanation = item.get("explanation", "")

        if not isinstance(question, str) or not question.strip():
            continue

        if not isinstance(options, list) or len(options) < 2:
            continue

        cleaned_options = []
        for option in options:
            if isinstance(option, str) and option.strip():
                cleaned_options.append(option.strip())

        if len(cleaned_options) < 2:
            continue

        cleaned_options = cleaned_options[:4]
        while len(cleaned_options) < 4:
            cleaned_options.append(cleaned_options[-1])

        if isinstance(correct_answer, int):
            idx = max(0, min(3, correct_answer))
        elif isinstance(correct_answer, str) and correct_answer.strip():
            try:
                idx = cleaned_options.index(correct_answer.strip())
            except ValueError:
                idx = 0
        else:
            idx = 0

        if not isinstance(explanation, str):
            explanation = str(explanation)

        normalized.append(
            {
                "id": i,
                "question": question.strip(),
                "options": cleaned_options,
                "correct_answer": idx,
                "explanation": explanation.strip(),
            }
        )

        if len(normalized) >= 5:
            break

    if not normalized:
        raise ValueError("Не удалось нормализовать вопросы")

    return {
        "title": data.get("title", "Домашнее задание"),
        "questions": normalized,
    }


def build_prompt(summary_text):
    return (
        "Составь небольшой тест по конспекту.\n"
        "Верни чистый текст без JSON и без markdown.\n"
        "Формат:\n"
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
        "Сделай 3-5 вопросов.\n\n"
        "Конспект:\n"
        f"{summary_text}\n\n"
        "Тест:"
    )


def generate_text(tokenizer, model, prompt, max_new_tokens):
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )

    decoded = tokenizer.decode(outputs[0], skip_special_tokens=True)
    if decoded.startswith(prompt):
        return decoded[len(prompt):].strip()
    return decoded.strip()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="summary.txt")
    parser.add_argument("--output", default="quiz.json")
    parser.add_argument("--raw-output", default="quiz_raw.txt")
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--max-new-tokens", type=int, default=900)
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def generate_quiz(
    summary_text,
    model_name="Qwen/Qwen2.5-0.5B-Instruct",
    max_new_tokens=400,
    retries=1,
):
    if not summary_text:
        raise ValueError("Конспект пустой")

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)

    prompt = build_prompt(summary_text)

    last_error = None
    last_raw_output = ""

    for attempt in range(1, retries + 2):
        print(f"[attempt {attempt}] generate quiz", file=sys.stderr)

        raw_output = generate_text(
            tokenizer,
            model,
            prompt,
            max_new_tokens,
        )

        last_raw_output = raw_output.strip()
        if last_raw_output:
            return last_raw_output

    return last_raw_output


def main():
    args = parse_args()

    try:
        summary_text = read_text(args.input)
        if not summary_text:
            raise ValueError("Файл с конспектом пустой")

        quiz = generate_quiz(
            summary_text,
            model_name=args.model,
            max_new_tokens=args.max_new_tokens,
            retries=args.retries,
        )
        write_json(args.output, quiz)
        print(json.dumps(quiz, ensure_ascii=False, indent=2))

    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
