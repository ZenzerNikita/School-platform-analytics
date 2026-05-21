import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List


METRIC_DEFINITIONS = [
    {
        "id": "interaction",
        "title": "Организация взаимодействия",
        "max_score": 2,
        "description": "Вовлекает студентов в процесс обучения, организует дискуссии и групповые задания.",
    },
    {
        "id": "structure",
        "title": "Структура занятия",
        "max_score": 1,
        "description": "Есть введение, основная часть, завершение и последовательная подача.",
    },
    {
        "id": "explanation",
        "title": "Объяснение материала",
        "max_score": 1,
        "description": "Есть примеры, аналогии, сторителлинг или разбор кейсов.",
    },
    {
        "id": "speech",
        "title": "Речь",
        "max_score": 1,
        "description": "Нет частых слов-паразитов, речевых ошибок и чрезмерно сложных фраз.",
    },
    {
        "id": "understanding_monitoring",
        "title": "Мониторинг понимания",
        "max_score": 1,
        "description": "Есть проверки понимания и вопросы на обратную связь.",
    },
    {
        "id": "examples_analogies",
        "title": "Примеры и аналогии",
        "max_score": 1,
        "description": "Преподаватель использует примеры, аналогии, кейсы или сравнения для объяснения.",
    },
    {
        "id": "audience_adaptation",
        "title": "Адаптация под аудиторию",
        "max_score": 1,
        "description": "Объяснение упрощается, переформулируется или подстраивается под уровень группы.",
    },
    {
        "id": "profanity",
        "title": "Нормативность речи",
        "max_score": 1,
        "description": "В речи не обнаружена ненормативная или грубо-разговорная лексика.",
    },
    {
        "id": "familiarity",
        "title": "Дистанция общения",
        "max_score": 1,
        "description": "Не наблюдается панибратского или чрезмерно фамильярного общения.",
    },
    {
        "id": "lesson_format",
        "title": "Формат занятия",
        "max_score": 1,
        "description": "Определяется формат занятия по признакам лекции, семинара, практики или дискуссии.",
    },
    {
        "id": "question_types",
        "title": "Типы вопросов",
        "max_score": 1,
        "description": "Определяются и различаются типы вопросов в учебном диалоге.",
    },
    {
        "id": "role_separation",
        "title": "Роли в диалоге",
        "max_score": 1,
        "description": "Различимы роли участников: преподаватель, студент, группа.",
    },
    {
        "id": "lesson_segmentation",
        "title": "Сегментация занятия",
        "max_score": 1,
        "description": "Определяются введение, основная часть и завершение занятия.",
    },
]

METRIC_BY_ID = {metric["id"]: metric for metric in METRIC_DEFINITIONS}

FILLER_WORDS = [
    "ну",
    "как бы",
    "типа",
    "значит",
    "короче",
    "в общем",
    "в принципе",
    "ээ",
    "эээ",
    "эм",
    "мм",
]

INTERACTION_PATTERNS = [
    r"\bкто\s+(может|хочет|готов)\b",
    r"\bдавайте\s+(обсудим|подумаем|разбер[её]м|попробуем)\b",
    r"\bобсудите\b",
    r"\bв\s+группах\b",
    r"\bпо\s+парам\b",
    r"\bответьте\b",
    r"\bкакие\s+есть\s+идеи\b",
    r"\bваше\s+мнение\b",
]

STRUCTURE_INTRO_PATTERNS = [
    r"\bсегодня\s+(мы\s+)?(разбер[её]м|изучим|поговорим)\b",
    r"\bтема\s+(занятия|урока|лекции)\b",
    r"\bцель\s+(занятия|урока|лекции)\b",
    r"\bначн[её]м\s+с\b",
]

STRUCTURE_MAIN_PATTERNS = [
    r"\bперейд[её]м\s+к\b",
    r"\bтеперь\s+(рассмотрим|разбер[её]м|поговорим)\b",
    r"\bво-первых\b",
    r"\bво-вторых\b",
    r"\bследующий\s+(пункт|этап|вопрос)\b",
]

STRUCTURE_END_PATTERNS = [
    r"\bподвед[её]м\s+итог\b",
    r"\bитак\b",
    r"\bв\s+заключение\b",
    r"\bрезюмируем\b",
    r"\bна\s+этом\s+завершим\b",
]

EXPLANATION_PATTERNS = [
    r"\bнапример\b",
    r"\bпример\b",
    r"\bаналогия\b",
    r"\bпредставьте\b",
    r"\bкак\s+если\s+бы\b",
    r"\bистория\b",
    r"\bкейс\b",
    r"\bситуация\b",
]

EXAMPLE_ANALOGY_PATTERNS = EXPLANATION_PATTERNS + [
    r"\bсравните\b",
    r"\bдопустим\b",
    r"\bпредположим\b",
    r"\bэто\s+похоже\s+на\b",
]

AUDIENCE_ADAPTATION_PATTERNS = [
    r"\bпростыми\s+словами\b",
    r"\bдругими\s+словами\b",
    r"\bесли\s+проще\b",
    r"\bесли\s+совсем\s+просто\b",
    r"\bдля\s+начинающих\b",
    r"\bнапомню\b",
    r"\bповторю\b",
    r"\bто\s+есть\b",
    r"\bесли\s+непонятно\b",
    r"\bесли\s+вы\s+только\s+начинаете\b",
]

UNDERSTANDING_PATTERNS = [
    r"\bпонятно\b",
    r"\bесть\s+вопросы\b",
    r"\bвопросы\s+есть\b",
    r"\bпроверьте\s+себя\b",
    r"\bчто\s+непонятно\b",
    r"\bповторите\b",
    r"\bкак\s+вы\s+поняли\b",
    r"\bкто\s+может\s+объяснить\b",
]

PROFANITY_PATTERNS = [
    r"\bблин\b",
    r"\bчерт\b",
    r"\bчёрт\b",
    r"\bофиге\w*\b",
    r"\bхрен\b",
    r"\bфигня\b",
    r"\bидиот\w*\b",
    r"\bтуп\w*\b",
]

FAMILIARITY_PATTERNS = [
    r"\bребят(а)?\b",
    r"\bребзя\b",
    r"\bдружище\b",
    r"\bчувак\w*\b",
    r"\bбрат\w*\b",
    r"\bсолнце\b",
    r"\bкотик\b",
    r"\bмалыш\w*\b",
]

LESSON_FORMAT_PATTERNS = {
    "lecture": [
        r"\bсегодня\s+мы\s+разбер[её]м\b",
        r"\bзапишите\b",
        r"\bопределение\b",
        r"\bлекци\w+\b",
        r"\bрассмотрим\s+тему\b",
    ],
    "seminar": [
        r"\bобсудим\b",
        r"\bваше\s+мнение\b",
        r"\bкто\s+хочет\s+ответить\b",
        r"\bразбер[её]м\s+вместе\b",
    ],
    "practice": [
        r"\bвыполните\b",
        r"\bрешите\b",
        r"\bупражнени\w+\b",
        r"\bзадач\w+\b",
        r"\bпрактик\w+\b",
    ],
    "discussion": [
        r"\bдискусси\w+\b",
        r"\bпоспорим\b",
        r"\bобсудите\b",
        r"\bв\s+группах\b",
        r"\bпо\s+парам\b",
    ],
}

QUESTION_TYPE_PATTERNS = {
    "open": [
        r"\bпочему\b",
        r"\bзачем\b",
        r"\bкак\b",
        r"\bкаким\s+образом\b",
        r"\bобъясните\b",
    ],
    "closed": [
        r"\bверно\s+ли\b",
        r"\bправда\s+ли\b",
        r"\bда\s+или\s+нет\b",
        r"\bможно\s+ли\b",
        r"\bполучится\s+ли\b",
    ],
    "clarifying": [
        r"\bчто\s+именно\b",
        r"\bуточните\b",
        r"\bправильно\s+ли\s+я\s+понял\b",
        r"\bможно\s+уточнить\b",
    ],
    "understanding_check": [
        r"\bпонятно\b",
        r"\bвсе\s+ли\s+ясно\b",
        r"\bкто\s+может\s+повторить\b",
        r"\bкак\s+вы\s+поняли\b",
        r"\bесть\s+вопросы\b",
    ],
    "rhetorical": [
        r"\bразве\b",
        r"\bнеужели\b",
        r"\bкто\s+же\s+этого\s+не\s+знает\b",
    ],
}

TEACHER_ROLE_PATTERNS = [
    r"^\s*(преподаватель|учитель|лектор)\s*[:\-]",
    r"\bя\s+объясню\b",
    r"\bдавайте\s+разбер[её]м\b",
]

STUDENT_ROLE_PATTERNS = [
    r"^\s*(студент|ученик|слушатель)\s*[:\-]",
    r"\bя\s+не\s+понял\b",
    r"\bможно\s+вопрос\b",
    r"\bа\s+почему\b",
]


def transcript_items_to_text(transcript: Any) -> str:
    if isinstance(transcript, str):
        return transcript.strip()

    if isinstance(transcript, list):
        parts = []
        for item in transcript:
            if isinstance(item, dict):
                text = item.get("text")
            else:
                text = item
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return " ".join(parts).strip()

    return ""


def normalize_transcript_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        transcript = payload.get("transcript")
    else:
        transcript = payload

    if isinstance(transcript, str):
        text = transcript.strip()
        if not text:
            raise ValueError("Транскрибация пустая.")
        return [{"start_ms": 0, "text": text}]

    if not isinstance(transcript, list):
        raise ValueError("Ожидалось поле transcript со списком фрагментов или строкой.")

    normalized = []
    for item in transcript:
        if isinstance(item, dict):
            text = item.get("text", "")
            start_ms = item.get("start_ms", item.get("start", 0))
            end_ms = item.get("end_ms")
        else:
            text = item
            start_ms = 0
            end_ms = None

        if not isinstance(text, str) or not text.strip():
            continue

        try:
            start_ms = int(start_ms or 0)
        except (TypeError, ValueError):
            start_ms = 0

        normalized_item = {
            "start_ms": start_ms,
            "text": text.strip(),
        }
        if end_ms is not None:
            try:
                normalized_item["end_ms"] = int(end_ms)
            except (TypeError, ValueError):
                pass
        normalized.append(normalized_item)

    if not normalized:
        raise ValueError("Транскрибация пустая.")

    return normalized


def analyze_transcript(transcript: Any) -> Dict[str, Any]:
    items = normalize_transcript_payload({"transcript": transcript})
    text = transcript_items_to_text(items)
    baseline = _heuristic_analysis(items, text)

    if _should_use_groq():
        try:
            return _merge_with_groq_analysis(items, baseline)
        except Exception as exc:
            baseline["fallback_reason"] = str(exc) or exc.__class__.__name__
            return baseline

    baseline["fallback_reason"] = _groq_disabled_reason()
    return baseline


def _heuristic_analysis(items: List[Dict[str, Any]], text: str) -> Dict[str, Any]:
    lowered = text.lower()
    sentence_lengths = _sentence_lengths(text)
    average_sentence_words = (
        round(sum(sentence_lengths) / len(sentence_lengths), 1) if sentence_lengths else 0
    )
    filler_hits = _count_filler_words(lowered)

    interaction_hits = _find_pattern_hits(items, INTERACTION_PATTERNS)
    intro_hits = _find_pattern_hits(items, STRUCTURE_INTRO_PATTERNS)
    main_hits = _find_pattern_hits(items, STRUCTURE_MAIN_PATTERNS)
    end_hits = _find_pattern_hits(items, STRUCTURE_END_PATTERNS)
    explanation_hits = _find_pattern_hits(items, EXPLANATION_PATTERNS)
    example_hits = _find_pattern_hits(items, EXAMPLE_ANALOGY_PATTERNS)
    adaptation_hits = _find_pattern_hits(items, AUDIENCE_ADAPTATION_PATTERNS)
    understanding_hits = _find_pattern_hits(items, UNDERSTANDING_PATTERNS)
    profanity_hits = _find_pattern_hits(items, PROFANITY_PATTERNS)
    familiarity_hits = _find_pattern_hits(items, FAMILIARITY_PATTERNS)
    lesson_format = _detect_lesson_format(items)
    question_summary = _classify_question_types(items)
    role_summary = _detect_roles(items)
    segmentation_summary = _build_segmentation_summary(intro_hits, main_hits, end_hits)
    supporting_fragments = _collect_supporting_fragments(
        interaction_hits,
        intro_hits,
        main_hits,
        end_hits,
        example_hits,
        adaptation_hits,
        understanding_hits,
        profanity_hits,
        familiarity_hits,
        role_summary["evidence"],
    )

    question_count = text.count("?")

    interaction_score = 0
    if interaction_hits or question_count >= 2:
        interaction_score = 1
    if len(interaction_hits) >= 2 or any(
        re.search(pattern, lowered)
        for pattern in [r"\bв\s+группах\b", r"\bпо\s+парам\b", r"\bобсудите\b"]
    ):
        interaction_score = 2

    structure_score = 1 if intro_hits and (main_hits or end_hits) else 0
    explanation_score = 1 if explanation_hits else 0
    speech_score = 1 if filler_hits <= 3 and average_sentence_words <= 28 else 0
    understanding_score = 1 if understanding_hits else 0
    examples_score = 1 if example_hits else 0
    adaptation_score = 1 if adaptation_hits else 0
    profanity_score = 0 if profanity_hits else 1
    familiarity_score = 0 if familiarity_hits else 1
    lesson_format_score = 1 if lesson_format["label"] != "Не определен" else 0
    question_types_score = 1 if question_summary["total"] > 0 else 0
    role_score = 1 if len(role_summary["detected_roles"]) >= 2 else 0
    segmentation_score = 1 if segmentation_summary["has_full_segmentation"] else 0

    metrics = [
        _metric(
            "interaction",
            interaction_score,
            _comment_for_interaction(interaction_score),
            interaction_hits,
            [f"Вопросов в транскрибации: {question_count}"],
        ),
        _metric(
            "structure",
            structure_score,
            _comment_binary(
                structure_score,
                "В транскрибации прослеживаются вводные и переходные/итоговые маркеры.",
                "В транскрибации недостаточно признаков введения, последовательной основной части и завершения.",
            ),
            intro_hits + main_hits + end_hits,
            [
                f"Введение: {len(intro_hits)}",
                f"Переходы/основная часть: {len(main_hits)}",
                f"Завершение: {len(end_hits)}",
            ],
        ),
        _metric(
            "explanation",
            explanation_score,
            _comment_binary(
                explanation_score,
                "Есть признаки объяснения через примеры, аналогии или ситуации.",
                "Не найдено явных примеров, аналогий или сторителлинга.",
            ),
            explanation_hits,
            [],
        ),
        _metric(
            "speech",
            speech_score,
            _comment_binary(
                speech_score,
                "Речь выглядит достаточно чистой по словам-паразитам и длине фраз.",
                "В речи заметны частые слова-паразиты или слишком длинные фразы.",
            ),
            _filler_evidence(items),
            [
                f"Слова-паразиты: {filler_hits}",
                f"Средняя длина фразы: {average_sentence_words} слов",
            ],
        ),
        _metric(
            "understanding_monitoring",
            understanding_score,
            _comment_binary(
                understanding_score,
                "Есть проверка понимания или запрос обратной связи.",
                "Не найдено явных проверок понимания.",
            ),
            understanding_hits,
            [],
        ),
        _metric(
            "examples_analogies",
            examples_score,
            _comment_binary(
                examples_score,
                "В объяснении есть примеры, аналогии или сопоставления.",
                "Явные примеры и аналогии в объяснении почти не проявлены.",
            ),
            example_hits,
            [],
        ),
        _metric(
            "audience_adaptation",
            adaptation_score,
            _comment_binary(
                adaptation_score,
                "Есть признаки упрощения и адаптации объяснения под уровень аудитории.",
                "Не найдено явных признаков адаптации объяснения под аудиторию.",
            ),
            adaptation_hits,
            [],
        ),
        _metric(
            "profanity",
            profanity_score,
            _comment_binary(
                profanity_score,
                "Ненормативная и грубо-разговорная лексика не обнаружена.",
                "Обнаружены маркеры нежелательной лексики.",
            ),
            profanity_hits,
            [
                "Проверка на грубую и ненормативную лексику выполнена.",
            ],
        ),
        _metric(
            "familiarity",
            familiarity_score,
            _comment_binary(
                familiarity_score,
                "Фамильярное или панибратское общение не выражено.",
                "Есть признаки чрезмерно фамильярного общения с аудиторией.",
            ),
            familiarity_hits,
            [],
        ),
        _metric(
            "lesson_format",
            lesson_format_score,
            _comment_binary(
                lesson_format_score,
                f"Формат занятия определяется как: {lesson_format['label']}.",
                "Формат занятия по транскрибации определить не удалось.",
            ),
            lesson_format["evidence"],
            lesson_format["signals"],
        ),
        _metric(
            "question_types",
            question_types_score,
            _comment_binary(
                question_types_score,
                "В диалоге различимы типы вопросов и их функции.",
                "Вопросы не обнаружены или их типы не различимы.",
            ),
            question_summary["examples"],
            question_summary["signals"],
        ),
        _metric(
            "role_separation",
            role_score,
            _comment_binary(
                role_score,
                f"В диалоге различимы роли: {', '.join(role_summary['detected_roles'])}.",
                "Роли в диалоге выражены слабо: транскрипт ближе к монологу или без явных маркеров участников.",
            ),
            role_summary["evidence"],
            role_summary["signals"],
        ),
        _metric(
            "lesson_segmentation",
            segmentation_score,
            _comment_binary(
                segmentation_score,
                "В транскрибации прослеживаются введение, основная часть и завершение.",
                "Полная сегментация занятия по транскрибации не прослеживается.",
            ),
            segmentation_summary["evidence"],
            segmentation_summary["signals"],
        ),
    ]

    total_score = sum(metric["score"] for metric in metrics)
    max_score = sum(metric["max_score"] for metric in metrics)

    return {
        "type": "lesson_analytics",
        "source": "heuristic",
        "total_score": total_score,
        "max_score": max_score,
        "metrics": metrics,
        "dialogue_analysis": {
            "lesson_format": lesson_format,
            "question_types": question_summary,
            "roles": role_summary,
            "segmentation": segmentation_summary,
        },
        "supporting_fragments": supporting_fragments,
        "recommendations": _build_recommendations(metrics),
    }


def _should_use_groq() -> bool:
    provider = os.getenv("ANALYTICS_PROVIDER") or os.getenv("LLM_PROVIDER", "")
    if provider.strip().lower() not in {"groq", "auto"}:
        return False
    return bool(os.getenv("GROQ_API_KEY") or os.getenv("API_KEY_STORAGE"))


def _groq_disabled_reason() -> str:
    provider = os.getenv("ANALYTICS_PROVIDER") or os.getenv("LLM_PROVIDER", "")
    if provider.strip().lower() not in {"groq", "auto"}:
        return (
            "Groq disabled: ANALYTICS_PROVIDER/LLM_PROVIDER must be 'groq' "
            f"or 'auto', got '{provider or '<empty>'}'."
        )
    if not (os.getenv("GROQ_API_KEY") or os.getenv("API_KEY_STORAGE")):
        return "Groq disabled: GROQ_API_KEY/API_KEY_STORAGE is not configured."
    return "Groq disabled."


def _merge_with_groq_analysis(
    items: List[Dict[str, Any]],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    raw = _call_groq(_analytics_prompt(items, baseline))
    parsed = _extract_json(raw)
    normalized = _normalize_llm_analysis(parsed)
    normalized["source"] = "groq"
    return normalized


def _call_groq(prompt: str) -> str:
    url = os.getenv("GROQ_PROXY_URL", "http://91.103.253.236/generate")
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("API_KEY_STORAGE")
    model = os.getenv("GROQ_ANALYTICS_MODEL") or os.getenv("GROQ_MODEL", "qwen/qwen3-32b")
    payload = {
        "prompt": prompt,
        "model": model,
        "temperature": 0.1,
        "max_tokens": 1400,
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
    timeout = float(os.getenv("GROQ_TIMEOUT_SEC", "60"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Groq proxy failed: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Groq proxy returned invalid payload")

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

    raise RuntimeError("Groq proxy response has no generated text")


def _analytics_prompt(items: List[Dict[str, Any]], baseline: Dict[str, Any]) -> str:
    transcript = "\n".join(
        f"[{_format_ms(item.get('start_ms', 0))}] {item['text']}" for item in items
    )
    transcript = transcript[:18000]
    rubric = "\n".join(
        f"- {metric['id']}: {metric['title']}, score 0-{metric['max_score']}. {metric['description']}"
        for metric in METRIC_DEFINITIONS
    )
    baseline_json = json.dumps(baseline, ensure_ascii=False)
    return (
        "Оцени качество проведения занятия по транскрибации.\n"
        "Верни только валидный JSON без markdown.\n"
        "Схема ответа:\n"
        "{\n"
        '  "type": "lesson_analytics",\n'
        '  "total_score": 0,\n'
        f'  "max_score": {sum(metric["max_score"] for metric in METRIC_DEFINITIONS)},\n'
        '  "metrics": [\n'
        '    {"id": "interaction", "title": "...", "score": 0, "max_score": 2, "comment": "...", "evidence": ["..."], "signals": ["..."]}\n'
        "  ],\n"
        '  "dialogue_analysis": {"lesson_format": {"label": "..."}, "question_types": {"signals": ["..."]}, "roles": {"detected_roles": ["..."]}, "segmentation": {"signals": ["..."]}},\n'
        '  "supporting_fragments": ["..."],\n'
        '  "recommendations": ["..."]\n'
        "}\n"
        "Допустимые id метрик строго такие: "
        + ", ".join(metric["id"] for metric in METRIC_DEFINITIONS)
        + ".\n"
        "Оценки должны быть только в допустимом диапазоне. Evidence бери короткими цитатами из транскрибации.\n\n"
        "Рубрика:\n"
        f"{rubric}\n\n"
        "Черновая эвристическая оценка, которую можно скорректировать:\n"
        f"{baseline_json}\n\n"
        "Транскрибация:\n"
        f"{transcript}"
    )


def _normalize_llm_analysis(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict) and isinstance(data.get("analytics"), dict):
        data = data["analytics"]
    if not isinstance(data, dict):
        raise ValueError("Analytics JSON must be an object")

    raw_metrics = data.get("metrics")
    if not isinstance(raw_metrics, list):
        raise ValueError("Analytics JSON has no metrics list")

    by_id = {}
    for raw_metric in raw_metrics:
        if not isinstance(raw_metric, dict):
            continue
        metric_id = str(raw_metric.get("id", "")).strip()
        definition = METRIC_BY_ID.get(metric_id)
        if not definition:
            continue
        max_score = definition["max_score"]
        score = _clamp_int(raw_metric.get("score"), 0, max_score)
        by_id[metric_id] = _metric(
            metric_id,
            score,
            str(raw_metric.get("comment") or "").strip()
            or "Оценка сформирована по транскрибации.",
            _string_list(raw_metric.get("evidence")),
            _string_list(raw_metric.get("signals")),
        )

    metrics = []
    for definition in METRIC_DEFINITIONS:
        metric = by_id.get(definition["id"])
        if metric:
            metrics.append(metric)
        else:
            metrics.append(
                _metric(
                    definition["id"],
                    0,
                    "Модель не вернула оценку для этой метрики.",
                    [],
                    [],
                )
            )

    total_score = sum(metric["score"] for metric in metrics)
    max_score = sum(metric["max_score"] for metric in metrics)
    recommendations = _string_list(data.get("recommendations")) or _build_recommendations(metrics)
    dialogue_analysis = data.get("dialogue_analysis")
    if not isinstance(dialogue_analysis, dict):
        dialogue_analysis = {}
    return {
        "type": "lesson_analytics",
        "total_score": total_score,
        "max_score": max_score,
        "metrics": metrics,
        "dialogue_analysis": dialogue_analysis,
        "supporting_fragments": _string_list(data.get("supporting_fragments")),
        "recommendations": recommendations,
    }


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start < 0:
        raise ValueError("No JSON object found")

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
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:index + 1])

    raise ValueError("JSON object is not balanced")


def _find_pattern_hits(items: List[Dict[str, Any]], patterns: List[str]) -> List[str]:
    hits = []
    for item in items:
        text = item["text"].strip()
        lowered = text.lower()
        if any(re.search(pattern, lowered) for pattern in patterns):
            hits.append(_shorten(text))
        if len(hits) >= 3:
            break
    return hits


def _count_filler_words(lowered_text: str) -> int:
    count = 0
    for word in FILLER_WORDS:
        count += len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", lowered_text))
    return count


def _filler_evidence(items: List[Dict[str, Any]]) -> List[str]:
    hits = []
    for item in items:
        lowered = item["text"].lower()
        if any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered) for word in FILLER_WORDS):
            hits.append(_shorten(item["text"]))
        if len(hits) >= 3:
            break
    return hits


def _sentence_lengths(text: str) -> List[int]:
    sentences = re.split(r"[.!?;]+", text)
    lengths = []
    for sentence in sentences:
        words = re.findall(r"[\wёЁа-яА-Я-]+", sentence)
        if words:
            lengths.append(len(words))
    return lengths


def _detect_lesson_format(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    score_by_format = {}
    evidence_by_format = {}
    for format_id, patterns in LESSON_FORMAT_PATTERNS.items():
        hits = _find_pattern_hits(items, patterns)
        score_by_format[format_id] = len(hits)
        evidence_by_format[format_id] = hits

    best_format = max(score_by_format, key=score_by_format.get)
    best_score = score_by_format[best_format]
    if best_score <= 0:
        return {
            "label": "Не определен",
            "signals": ["Недостаточно маркеров формата занятия."],
            "evidence": [],
        }

    labels = {
        "lecture": "Лекция",
        "seminar": "Семинар",
        "practice": "Практика",
        "discussion": "Дискуссия",
    }
    return {
        "label": labels[best_format],
        "signals": [f"{labels[key]}: {value}" for key, value in score_by_format.items() if value > 0][:4],
        "evidence": evidence_by_format[best_format][:3],
    }


def _classify_question_types(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {question_type: 0 for question_type in QUESTION_TYPE_PATTERNS}
    examples: List[str] = []
    for item in items:
        text = item["text"].strip()
        lowered = text.lower()
        matched = []
        for question_type, patterns in QUESTION_TYPE_PATTERNS.items():
            if any(re.search(pattern, lowered) for pattern in patterns):
                counts[question_type] += 1
                matched.append(question_type)
        if matched:
            examples.append(_shorten(text))
        if len(examples) >= 3:
            break

    labels = {
        "open": "Открытые",
        "closed": "Закрытые",
        "clarifying": "Уточняющие",
        "understanding_check": "Проверка понимания",
        "rhetorical": "Риторические",
    }
    signals = [
        f"{labels[question_type]}: {count}"
        for question_type, count in counts.items()
        if count > 0
    ]
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "signals": signals[:5],
        "examples": examples,
    }


def _detect_roles(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    teacher_hits = _find_pattern_hits(items, TEACHER_ROLE_PATTERNS)
    student_hits = _find_pattern_hits(items, STUDENT_ROLE_PATTERNS)
    detected_roles = []
    if teacher_hits:
        detected_roles.append("преподаватель")
    if student_hits:
        detected_roles.append("студент")
    if len(detected_roles) < 2 and any("?" in item["text"] for item in items):
        detected_roles.append("группа")
    return {
        "detected_roles": detected_roles,
        "signals": [
            f"Маркеры преподавателя: {len(teacher_hits)}",
            f"Маркеры студента: {len(student_hits)}",
        ],
        "evidence": (teacher_hits + student_hits)[:3],
    }


def _build_segmentation_summary(
    intro_hits: List[str],
    main_hits: List[str],
    end_hits: List[str],
) -> Dict[str, Any]:
    return {
        "has_intro": bool(intro_hits),
        "has_main": bool(main_hits),
        "has_end": bool(end_hits),
        "has_full_segmentation": bool(intro_hits and main_hits and end_hits),
        "signals": [
            f"Введение: {'да' if intro_hits else 'нет'}",
            f"Основная часть: {'да' if main_hits else 'нет'}",
            f"Завершение: {'да' if end_hits else 'нет'}",
        ],
        "evidence": (intro_hits[:1] + main_hits[:1] + end_hits[:1])[:3],
    }


def _collect_supporting_fragments(*fragment_groups: List[str]) -> List[str]:
    seen = set()
    result = []
    for group in fragment_groups:
        for item in group:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
            if len(result) >= 8:
                return result
    return result


def _metric(
    metric_id: str,
    score: int,
    comment: str,
    evidence: List[str],
    signals: List[str],
) -> Dict[str, Any]:
    definition = METRIC_BY_ID[metric_id]
    return {
        "id": metric_id,
        "title": definition["title"],
        "score": _clamp_int(score, 0, definition["max_score"]),
        "max_score": definition["max_score"],
        "comment": comment,
        "evidence": evidence[:3],
        "signals": signals[:4],
    }


def _build_recommendations(metrics: List[Dict[str, Any]]) -> List[str]:
    recommendations = []
    for metric in metrics:
        if metric["score"] >= metric["max_score"]:
            continue
        if metric["id"] == "interaction":
            recommendations.append("Добавить вопросы аудитории, короткие обсуждения или задания в парах/группах.")
        elif metric["id"] == "structure":
            recommendations.append("Явно обозначать цель занятия, переходы между частями и итог в конце.")
        elif metric["id"] == "explanation":
            recommendations.append("Дополнять объяснение примерами, аналогиями или практическими ситуациями.")
        elif metric["id"] == "speech":
            recommendations.append("Сократить слова-паразиты и дробить длинные фразы на более короткие.")
        elif metric["id"] == "understanding_monitoring":
            recommendations.append("Регулярно проверять понимание: задавать вопросы и просить студентов сформулировать вывод.")
        elif metric["id"] == "examples_analogies":
            recommendations.append("Добавить больше примеров, аналогий и прикладных кейсов для сложных мест.")
        elif metric["id"] == "audience_adaptation":
            recommendations.append("Чаще переформулировать сложные места простыми словами и подстраивать темп под аудиторию.")
        elif metric["id"] == "profanity":
            recommendations.append("Исключить грубую и ненормативную лексику из учебной коммуникации.")
        elif metric["id"] == "familiarity":
            recommendations.append("Сдержаннее использовать фамильярные обращения и держать профессиональную дистанцию.")
        elif metric["id"] == "lesson_format":
            recommendations.append("Явнее обозначать формат занятия и согласовывать под него способы работы с аудиторией.")
        elif metric["id"] == "question_types":
            recommendations.append("Балансировать типы вопросов: добавлять открытые, уточняющие и вопросы на понимание.")
        elif metric["id"] == "role_separation":
            recommendations.append("Явнее фиксировать реплики преподавателя и студентов, чтобы диалог был структурнее.")
        elif metric["id"] == "lesson_segmentation":
            recommendations.append("Сильнее отделять введение, основную часть и завершение с помощью речевых маркеров.")
    if not recommendations:
        recommendations.append("Сохранить текущую структуру занятия и продолжать использовать активные приемы обучения.")
    return recommendations[:5]


def _comment_for_interaction(score: int) -> str:
    if score == 2:
        return "Есть регулярное вовлечение студентов или явные групповые/дискуссионные задания."
    if score == 1:
        return "Есть отдельные признаки вовлечения студентов, но их немного."
    return "Не найдено явных признаков вовлечения студентов в обсуждение или групповую работу."


def _comment_binary(score: int, positive: str, negative: str) -> str:
    return positive if score else negative


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:5]


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        integer = int(value)
    except (TypeError, ValueError):
        integer = minimum
    return max(minimum, min(maximum, integer))


def _shorten(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _format_ms(value: Any) -> str:
    try:
        seconds = max(0, int(value or 0) // 1000)
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
