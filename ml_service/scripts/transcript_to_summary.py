import argparse
import sys
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def split_text(text, chunk_size, overlap):
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start = max(end - overlap, start + 1)

    return chunks


def generate_text(tokenizer, model, prompt, max_new_tokens):
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )

    result = tokenizer.decode(outputs[0], skip_special_tokens=True)
    return result[len(prompt):].strip() if result.startswith(prompt) else result.strip()


def summarize_chunk(tokenizer, model, chunk, max_new_tokens):
    prompt = (
        "Сделай краткое структурированное резюме фрагмента расшифровки на русском языке.\n"
        "Верни только результат.\n\n"
        "Требования:\n"
        "- убрать повторы, слова-паразиты и шум устной речи\n"
        "- сохранить ключевые факты и цифры\n"
        "- если есть задачи, вынеси их отдельным пунктом\n\n"
        "Текст:\n"
        f"{chunk}\n\n"
        "Конспект:"
    )
    return generate_text(tokenizer, model, prompt, max_new_tokens)


def summarize_final(tokenizer, model, partial_summaries, max_new_tokens):
    joined = "\n\n".join(
        f"Фрагмент {i + 1}:\n{summary}"
        for i, summary in enumerate(partial_summaries)
    )

    prompt = (
        "Ниже даны промежуточные резюме частей одной расшифровки.\n"
        "Собери из них один итоговый конспект на русском языке.\n"
        "Верни только итоговый текст.\n\n"
        "Требования:\n"
        "- коротко и по делу\n"
        "- не повторяй одно и то же разными словами\n\n"
        f"{joined}\n\n"
        "Итоговый конспект:"
    )
    return generate_text(tokenizer, model, prompt, max_new_tokens)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="transcript.txt")
    parser.add_argument("--output", default="summary.txt")
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--chunk-size", type=int, default=2500)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--max-new-tokens", type=int, default=220)
    parser.add_argument("--final-max-new-tokens", type=int, default=300)
    return parser.parse_args()


def summarize_text(
    text,
    model_name="Qwen/Qwen2.5-0.5B-Instruct",
    chunk_size=2500,
    chunk_overlap=200,
    max_new_tokens=220,
    final_max_new_tokens=300,
):
    if not text:
        raise ValueError("Текст пустой")

    chunks = split_text(text, chunk_size, chunk_overlap)
    if not chunks:
        raise ValueError("Не удалось разбить текст на части")

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)

    partial_summaries = []
    for chunk in chunks:
        summary = summarize_chunk(tokenizer, model, chunk, max_new_tokens)
        partial_summaries.append(summary)

    final_summary = summarize_final(
        tokenizer,
        model,
        partial_summaries,
        final_max_new_tokens,
    )
    return final_summary


def main():
    args = parse_args()

    try:
        text = read_text(args.input)
        if not text:
            raise ValueError("Файл с транскриптом пустой")

        final_summary = summarize_text(
            text,
            model_name=args.model,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            max_new_tokens=args.max_new_tokens,
            final_max_new_tokens=args.final_max_new_tokens,
        )

        write_text(args.output, final_summary)

    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
