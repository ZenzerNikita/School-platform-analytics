import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

try:
    from scripts.lesson_analytics import METRIC_DEFINITIONS, analyze_transcript
except ModuleNotFoundError:
    from lesson_analytics import METRIC_DEFINITIONS, analyze_transcript


METRIC_IDS = [metric["id"] for metric in METRIC_DEFINITIONS]
MAX_SCORES = {metric["id"]: metric["max_score"] for metric in METRIC_DEFINITIONS}

DEFAULT_TARGET_PROFILES = [
    {"interaction": 0, "structure": 0, "explanation": 0, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 0, "structure": 0, "explanation": 0, "speech": 0, "understanding_monitoring": 1},
    {"interaction": 0, "structure": 0, "explanation": 0, "speech": 1, "understanding_monitoring": 0},
    {"interaction": 0, "structure": 0, "explanation": 1, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 0, "structure": 1, "explanation": 0, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 1, "structure": 0, "explanation": 0, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 1, "structure": 1, "explanation": 0, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 1, "structure": 0, "explanation": 1, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 1, "structure": 0, "explanation": 0, "speech": 1, "understanding_monitoring": 0},
    {"interaction": 1, "structure": 0, "explanation": 0, "speech": 0, "understanding_monitoring": 1},
    {"interaction": 1, "structure": 1, "explanation": 1, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 1, "structure": 1, "explanation": 0, "speech": 1, "understanding_monitoring": 1},
    {"interaction": 2, "structure": 0, "explanation": 0, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 2, "structure": 1, "explanation": 0, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 2, "structure": 0, "explanation": 1, "speech": 0, "understanding_monitoring": 1},
    {"interaction": 2, "structure": 1, "explanation": 1, "speech": 0, "understanding_monitoring": 0},
    {"interaction": 2, "structure": 1, "explanation": 0, "speech": 1, "understanding_monitoring": 1},
    {"interaction": 2, "structure": 0, "explanation": 1, "speech": 1, "understanding_monitoring": 1},
    {"interaction": 2, "structure": 1, "explanation": 1, "speech": 1, "understanding_monitoring": 0},
    {"interaction": 2, "structure": 1, "explanation": 1, "speech": 1, "understanding_monitoring": 1},
]


def main() -> int:
    args = parse_args()

    if args.generate:
        cases = generate_cases(
            count=args.cases,
            batch_size=args.batch_size,
            pause_sec=args.generate_sleep,
            raw_generations_file=args.raw_generations_file,
            transcript_chars=args.transcript_chars,
        )
        write_json(args.cases_file, {"cases": cases})
    else:
        cases = read_cases(args.cases_file)

    report = evaluate_cases(cases, analyzer=args.analyzer, pause_sec=args.analyze_sleep)
    write_json(args.report, report)
    print_report(report, args.report)
    return 0 if report["summary"]["metric_accuracy"] >= args.min_metric_accuracy else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic lesson transcripts and evaluate lesson analytics scoring."
    )
    parser.add_argument("--generate", action="store_true", help="Generate cases with Groq before evaluation.")
    parser.add_argument("--cases", type=int, default=20, help="Number of generated cases.")
    parser.add_argument("--batch-size", type=int, default=4, help="Number of cases per Groq generation call.")
    parser.add_argument("--cases-file", default="lesson_analytics_cases.json", help="Where generated cases are stored.")
    parser.add_argument("--report", default="lesson_analytics_eval_report.json", help="Where evaluation report is stored.")
    parser.add_argument(
        "--raw-generations-file",
        default="lesson_analytics_generation_raw.jsonl",
        help="Where raw Groq generation responses are appended.",
    )
    parser.add_argument(
        "--analyzer",
        choices=["current", "heuristic", "groq"],
        default="current",
        help="Which analyzer mode to use while scoring cases.",
    )
    parser.add_argument("--generate-sleep", type=float, default=2.0, help="Pause between generation calls.")
    parser.add_argument("--analyze-sleep", type=float, default=1.0, help="Pause between analyze calls.")
    parser.add_argument(
        "--transcript-chars",
        type=int,
        default=30000,
        help="Target transcript length in characters for generated cases.",
    )
    parser.add_argument(
        "--min-metric-accuracy",
        type=float,
        default=0.0,
        help="Exit with code 1 if metric accuracy is lower than this value.",
    )
    return parser.parse_args()


def generate_cases(
    count: int,
    batch_size: int,
    pause_sec: float,
    raw_generations_file: str,
    transcript_chars: int,
) -> List[Dict[str, Any]]:
    profiles = build_target_profiles(count)
    all_cases = []
    for start in range(0, len(profiles), batch_size):
        batch = profiles[start:start + batch_size]
        prompt = build_generation_prompt(
            batch,
            start_index=start + 1,
            transcript_chars=transcript_chars,
        )
        raw = call_groq(
            prompt,
            max_tokens=max(1600, int(transcript_chars * 0.55) * len(batch)),
        )
        raw_record = {
            "batch_start": start + 1,
            "expected_profiles": batch,
            "raw": raw,
        }
        try:
            parsed = extract_json(raw)
            batch_cases = normalize_generated_cases(parsed, expected_profiles=batch, start_index=start + 1)
            raw_record["parse_status"] = "ok"
        except Exception as exc:
            raw_record["parse_status"] = "failed"
            raw_record["parse_error"] = str(exc)
            batch_cases = raw_text_to_cases(
                raw,
                expected_profiles=batch,
                start_index=start + 1,
                parse_error=str(exc),
            )
        append_jsonl(raw_generations_file, raw_record)
        all_cases.extend(batch_cases)
        if pause_sec and start + batch_size < len(profiles):
            time.sleep(pause_sec)
    return all_cases[:count]


def build_target_profiles(count: int) -> List[Dict[str, int]]:
    profiles = []
    while len(profiles) < count:
        profiles.extend(DEFAULT_TARGET_PROFILES)
    return profiles[:count]


def build_generation_prompt(
    profiles: List[Dict[str, int]],
    start_index: int,
    transcript_chars: int,
) -> str:
    profiles_json = json.dumps(
        [
            {
                "id": f"case_{start_index + index}",
                "expected": profile,
            }
            for index, profile in enumerate(profiles)
        ],
        ensure_ascii=False,
        indent=2,
    )
    rubric = "\n".join(
        f"- {metric['id']}: score 0-{metric['max_score']}. {metric['description']}"
        for metric in METRIC_DEFINITIONS
    )
    return (
        "Сгенерируй synthetic dataset для проверки анализатора качества занятия.\n"
        "Нужно написать реалистичные русскоязычные транскрибации уроков под заранее заданные оценки.\n"
        "Верни только валидный JSON без markdown.\n"
        "Формат:\n"
        "{\n"
        '  "cases": [\n'
        '    {"id": "case_1", "expected": {"interaction": 0, "structure": 0, "explanation": 0, "speech": 0, "understanding_monitoring": 0}, "transcript": "..."}\n'
        "  ]\n"
        "}\n\n"
        "Правила:\n"
        f"- transcript должен быть примерно {transcript_chars} символов, допустимое отклонение 15%.\n"
        "- transcript должен выглядеть как полная расшифровка урока с репликами преподавателя и студентов.\n"
        "- Используй много коротких реплик, временные переходы урока и естественные учебные ситуации.\n"
        "- transcript не должен явно называть оценки или критерии.\n"
        "- expected в ответе должен точно совпадать с заданным expected.\n"
        "- Для низких оценок специально покажи отсутствие нужного поведения.\n"
        "- Для высоких оценок явно покажи признаки нужного поведения в речи.\n\n"
        "Рубрика:\n"
        f"{rubric}\n\n"
        "Целевые кейсы:\n"
        f"{profiles_json}"
    )


def call_groq(prompt: str, max_tokens: int) -> str:
    url = os.getenv("GROQ_PROXY_URL", "http://91.103.253.236/generate")
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("API_KEY_STORAGE")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY/API_KEY_STORAGE is required to generate cases.")
    model = os.getenv("GROQ_EVAL_MODEL") or os.getenv("GROQ_ANALYTICS_MODEL") or os.getenv("GROQ_MODEL", "qwen/qwen3-32b")
    payload = {
        "prompt": prompt,
        "model": model,
        "temperature": 0.8,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )
    timeout = float(os.getenv("GROQ_TIMEOUT_SEC", "90"))
    retries = int(os.getenv("GROQ_RETRIES", "2"))
    retry_sleep = float(os.getenv("GROQ_RETRY_SLEEP_SEC", "20"))
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
                continue
            raise RuntimeError(f"Groq generation failed: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Groq generation failed: {exc}") from exc

    return generated_text_from_payload(data)


def generated_text_from_payload(data: Any) -> str:
    if not isinstance(data, dict):
        raise RuntimeError("Groq returned non-object payload.")
    for key in ("text", "response", "output", "content", "result"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(first.get("text"), str):
                return first["text"].strip()
    raise RuntimeError("Groq payload has no generated text.")


def normalize_generated_cases(
    data: Any,
    expected_profiles: List[Dict[str, int]],
    start_index: int,
) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        raw_cases = data.get("cases")
    else:
        raw_cases = data
    if not isinstance(raw_cases, list):
        raise ValueError("Generated JSON must contain a cases list.")

    cases = []
    for index, expected in enumerate(expected_profiles):
        raw_case = raw_cases[index] if index < len(raw_cases) else {}
        if not isinstance(raw_case, dict):
            raw_case = {}
        transcript = str(raw_case.get("transcript") or "").strip()
        if not transcript:
            raise ValueError(f"Generated case_{start_index + index} has empty transcript.")
        cases.append(
            {
                "id": str(raw_case.get("id") or f"case_{start_index + index}"),
                "expected": normalize_scores(raw_case.get("expected") or expected),
                "transcript": transcript,
            }
        )

    for index, expected in enumerate(expected_profiles):
        cases[index]["expected"] = normalize_scores(expected)
    return cases


def raw_text_to_cases(
    raw: str,
    expected_profiles: List[Dict[str, int]],
    start_index: int,
    parse_error: str,
) -> List[Dict[str, Any]]:
    chunks = split_raw_cases(raw, len(expected_profiles))
    cases = []
    for index, expected in enumerate(expected_profiles):
        transcript = chunks[index] if index < len(chunks) else raw
        transcript = cleanup_raw_transcript(transcript)
        if not transcript:
            transcript = cleanup_raw_transcript(raw)
        cases.append(
            {
                "id": f"case_{start_index + index}",
                "expected": normalize_scores(expected),
                "transcript": transcript,
                "generation_parse_error": parse_error,
                "generation_source": "raw_groq_output",
            }
        )
    return cases


def split_raw_cases(raw: str, expected_count: int) -> List[str]:
    if expected_count <= 1:
        return [raw]

    pattern = re.compile(r"(?:^|\n)\s*(?:case[_\s-]*\d+|кейс\s*\d+)[:.\-\s]", re.IGNORECASE)
    matches = list(pattern.finditer(raw))
    if len(matches) >= expected_count:
        chunks = []
        for index, match in enumerate(matches[:expected_count]):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            chunks.append(raw[start:end].strip())
        return chunks

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]
    if len(paragraphs) >= expected_count:
        return paragraphs[:expected_count]

    return [raw for _ in range(expected_count)]


def cleanup_raw_transcript(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?|```", "", text, flags=re.IGNORECASE)
    text = text.strip()

    transcript_match = re.search(
        r'"transcript"\s*:\s*"(?P<value>.*?)(?<!\\)"',
        text,
        flags=re.DOTALL,
    )
    if transcript_match:
        value = transcript_match.group("value")
        try:
            return json.loads(f'"{value}"').strip()
        except json.JSONDecodeError:
            return value.replace("\\n", "\n").replace('\\"', '"').strip()

    text = re.sub(r'"expected"\s*:\s*\{.*?\},?', "", text, flags=re.DOTALL)
    text = re.sub(r'"id"\s*:\s*"[^"]+",?', "", text)
    text = re.sub(r'^\s*[\{\[\]},]+\s*$', "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def evaluate_cases(cases: List[Dict[str, Any]], analyzer: str, pause_sec: float) -> Dict[str, Any]:
    original_provider = os.environ.get("ANALYTICS_PROVIDER")
    original_llm_provider = os.environ.get("LLM_PROVIDER")
    if analyzer == "heuristic":
        os.environ["ANALYTICS_PROVIDER"] = "heuristic"
        os.environ["LLM_PROVIDER"] = "heuristic"
    elif analyzer == "groq":
        os.environ["ANALYTICS_PROVIDER"] = "groq"

    try:
        results = []
        for index, case in enumerate(cases):
            analytics = analyze_transcript(case["transcript"])
            predicted = scores_from_analytics(analytics)
            expected = normalize_scores(case["expected"])
            comparisons = {
                metric_id: {
                    "expected": expected[metric_id],
                    "predicted": predicted.get(metric_id),
                    "match": expected[metric_id] == predicted.get(metric_id),
                }
                for metric_id in METRIC_IDS
            }
            results.append(
                {
                    "id": case["id"],
                    "expected": expected,
                    "predicted": predicted,
                    "matches_all": all(item["match"] for item in comparisons.values()),
                    "comparisons": comparisons,
                    "source": analytics.get("source"),
                    "fallback_reason": analytics.get("fallback_reason"),
                    "transcript": case["transcript"],
                    "analytics": analytics,
                }
            )
            if pause_sec and index + 1 < len(cases):
                time.sleep(pause_sec)
    finally:
        restore_env("ANALYTICS_PROVIDER", original_provider)
        restore_env("LLM_PROVIDER", original_llm_provider)

    return build_report(results, analyzer=analyzer)


def build_report(results: List[Dict[str, Any]], analyzer: str) -> Dict[str, Any]:
    total_cases = len(results)
    total_metric_checks = total_cases * len(METRIC_IDS)
    matched_metric_checks = sum(
        1
        for result in results
        for comparison in result["comparisons"].values()
        if comparison["match"]
    )
    exact_cases = sum(1 for result in results if result["matches_all"])
    per_metric = {}
    for metric_id in METRIC_IDS:
        matches = sum(1 for result in results if result["comparisons"][metric_id]["match"])
        per_metric[metric_id] = {
            "matches": matches,
            "total": total_cases,
            "accuracy": round(matches / total_cases, 4) if total_cases else 0.0,
        }

    return {
        "summary": {
            "analyzer": analyzer,
            "total_cases": total_cases,
            "exact_case_matches": exact_cases,
            "case_accuracy": round(exact_cases / total_cases, 4) if total_cases else 0.0,
            "matched_metric_checks": matched_metric_checks,
            "total_metric_checks": total_metric_checks,
            "metric_accuracy": round(matched_metric_checks / total_metric_checks, 4)
            if total_metric_checks
            else 0.0,
            "per_metric": per_metric,
        },
        "results": results,
    }


def scores_from_analytics(analytics: Dict[str, Any]) -> Dict[str, int]:
    scores = {}
    for metric in analytics.get("metrics", []):
        if isinstance(metric, dict) and metric.get("id") in METRIC_IDS:
            scores[metric["id"]] = metric.get("score")
    return scores


def normalize_scores(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        value = {}
    normalized = {}
    for metric_id in METRIC_IDS:
        score = value.get(metric_id, 0)
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = 0
        normalized[metric_id] = max(0, min(MAX_SCORES[metric_id], score))
    return normalized


def extract_json(text: str) -> Any:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start < 0:
        start = cleaned.find("[")
    if start < 0:
        raise ValueError("No JSON object or array found.")

    open_char = cleaned[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:index + 1])

    raise ValueError("JSON is not balanced.")


def read_cases(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{path} must contain a non-empty cases list.")
    cases = []
    for index, raw_case in enumerate(raw_cases, 1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"Case #{index} must be an object.")
        transcript = str(raw_case.get("transcript") or "").strip()
        if not transcript:
            raise ValueError(f"Case #{index} has empty transcript.")
        cases.append(
            {
                "id": str(raw_case.get("id") or f"case_{index}"),
                "expected": normalize_scores(raw_case.get("expected")),
                "transcript": transcript,
            }
        )
    return cases


def write_json(path: str, data: Any) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_jsonl(path: str, data: Any) -> None:
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False))
        file.write("\n")


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def print_report(report: Dict[str, Any], report_path: str) -> None:
    summary = report["summary"]
    print(f"Cases: {summary['total_cases']}")
    print(f"Exact case accuracy: {summary['case_accuracy']:.2%}")
    print(f"Metric accuracy: {summary['metric_accuracy']:.2%}")
    for metric_id, metric_summary in summary["per_metric"].items():
        print(f"- {metric_id}: {metric_summary['accuracy']:.2%}")
    mismatches = [result for result in report["results"] if not result["matches_all"]]
    if mismatches:
        print("Mismatches:")
        for result in mismatches[:10]:
            failed = [
                f"{metric_id} expected={comparison['expected']} predicted={comparison['predicted']}"
                for metric_id, comparison in result["comparisons"].items()
                if not comparison["match"]
            ]
            print(f"- {result['id']}: " + "; ".join(failed))
    print(f"Report: {report_path}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
