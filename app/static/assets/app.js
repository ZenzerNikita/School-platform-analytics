const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadStatusDiv = document.getElementById("uploadStatus");
const generateBtn = document.getElementById("generateBtn");
const resetUploadBtn = document.getElementById("resetUploadBtn");
const transcriptContainer = document.getElementById("transcriptContainer");
const transcriptJumpBtn = document.getElementById("transcriptJumpBtn");
const summaryContainer = document.getElementById("summaryContainer");
const quizContainer = document.getElementById("quizContainer");
const analyticsContainer = document.getElementById("analyticsContainer");
const summaryTabBtn = document.getElementById("summaryTabBtn");
const quizTabBtn = document.getElementById("quizTabBtn");
const analyticsTabBtn = document.getElementById("analyticsTabBtn");
const editSummaryBtn = document.getElementById("editSummaryBtn");
const editQuizBtn = document.getElementById("editQuizBtn");
const historyToggleBtn = document.getElementById("historyToggleBtn");
const historySidebar = document.querySelector(".history-sidebar");
const historyDrawer = document.getElementById("historyDrawer");
const historyOverlay = document.getElementById("historyOverlay");
const historyList = document.getElementById("historyList");
const historyListMobile = document.getElementById("historyListMobile");
const closeHistoryBtn = document.getElementById("closeHistoryBtn");
const tabBtns = document.querySelectorAll(".tab-btn");
const panels = {
  transcript: document.getElementById("panelTranscript"),
  summary: document.getElementById("panelSummary"),
  quiz: document.getElementById("panelQuiz"),
  analytics: document.getElementById("panelAnalytics"),
};

let fileToUpload = null;
let streamWS = null;
let streamActive = false;
let streamFailed = false;
let initAcked = false;
let lastTask = null;
let currentTaskId = "";
let quizEditorState = null;
let quizEditorDirty = false;
let quizEditorTaskId = "";
let streamCompletedSuccessfully = false;
let analyticsPollTimer = null;

const requestedTaskId = new URL(window.location.href).searchParams.get("task_id") || "";

const ERROR_FALLBACKS = {
  400: "Не удалось обработать файл. Проверьте формат записи и попробуйте снова.",
  500: "Ошибка обработки на сервере. Попробуйте повторить позже.",
  503: "ML-сервис временно недоступен. Попробуйте позже.",
};

function escapeHtml(str) {
  return (str || "").replace(/[&<>\"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[m]));
}

function protectMathSegments(text) {
  const mathParts = [];
  const protectedText = (text || "").replace(/\$\$[\s\S]*?\$\$|\$[^$\n]+?\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g, (match) => {
    const token = `@@MATH_${mathParts.length}@@`;
    mathParts.push(match);
    return token;
  });
  return { text: protectedText, mathParts };
}

function restoreMathSegments(text, mathParts) {
  return text.replace(/@@MATH_(\d+)@@/g, (_match, idx) => mathParts[Number(idx)] || "");
}

function normalizeTextBreaks(text) {
  return (text || "").replace(/\\n/g, "\n");
}

function removePunctuationAfterBlockMath(text) {
  return (text || "").replace(/\$\$[\s\S]*?\$\$[\s]*[.,;:!?]+/g, (match) => {
    const mathEnd = match.lastIndexOf("$$");
    return match.slice(0, mathEnd + 2);
  });
}

function formatMarkdownToHtml(text) {
  const source = escapeHtml(normalizeTextBreaks(removePunctuationAfterBlockMath(text)));
  const protectedMath = protectMathSegments(source);
  let html = protectedMath.text;

  const codeParts = [];
  html = html.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, (_match, lang, code) => {
    const token = `@@CODE_${codeParts.length}@@`;
    codeParts.push({ lang: lang || "plaintext", code });
    return token;
  });

  const blocks = html.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  const formattedBlocks = blocks.map((block) => {
    if (block.includes("@@CODE_")) {
      return block.replace(/@@CODE_(\d+)@@/g, (_m, idx) => {
        const part = codeParts[Number(idx)];
        if (!part) return "";
        const langClass = part.lang && part.lang !== "plaintext" ? `language-${part.lang}` : "";
        return `<pre class="code-block"><code class="${langClass}">${part.code}</code></pre>`;
      });
    }

    const listMatch = block.match(/^(?:\* .+(?:\n|$))+$/);
    if (listMatch) {
      const items = block
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.startsWith("* "))
        .map((line) => `<li>${line.slice(2).trim()}</li>`)
        .join("");
      return `<ul>${items}</ul>`;
    }

    const content = block
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/\n/g, "<br>");
    return `<p>${content}</p>`;
  });

  return restoreMathSegments(formattedBlocks.join(""), protectedMath.mathParts);
}

function renderMathInContainer(container) {
  if (!container || !window.renderMathInElement) return;
  if (!container.innerHTML.trim()) return;
  try {
    window.renderMathInElement(container, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\(", right: "\\)", display: false },
        { left: "\\[", right: "\\]", display: true },
      ],
      throwOnError: false,
    });
    container.querySelectorAll(".katex-display").forEach((node) => {
      if (node.parentElement && !node.parentElement.classList.contains("math-scroll-wrap")) {
        const wrapper = document.createElement("div");
        wrapper.className = "math-scroll-wrap";
        node.parentElement.insertBefore(wrapper, node);
        wrapper.appendChild(node);
      }
    });
  } catch (_e) {
    // ignore
  }
}

function highlightCodeInContainer(container) {
  if (!container || !window.hljs) return;
  container.querySelectorAll("pre.code-block code").forEach((block) => {
    try {
      window.hljs.highlightElement(block);
    } catch (_e) {
      // ignore
    }
  });
}

function formatTime(sec) {
  const safeSec = Number(sec || 0);
  const m = Math.floor(safeSec / 60).toString().padStart(2, "0");
  const s = Math.floor(safeSec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function setStatusHtml(html) {
  uploadStatusDiv.innerHTML = html || "";
}

function setStatus(message, loading = false) {
  if (!message) {
    setStatusHtml("");
    return;
  }
  setStatusHtml(`
    <div class="file-info">
      ${loading ? '<span class="loader"></span>' : ""}
      <span>${message}</span>
    </div>
  `);
}

function getErrorMessage(msg) {
  const statusCode = Number(msg?.status_code || msg?.error_status_code || 0);
  if (msg?.detail) {
    return `${msg.detail}${statusCode ? ` (${statusCode})` : ""}`;
  }
  if (msg?.error) {
    return `${msg.error}${statusCode ? ` (${statusCode})` : ""}`;
  }
  if (ERROR_FALLBACKS[statusCode]) {
    return ERROR_FALLBACKS[statusCode];
  }
  return "Не удалось завершить обработку файла.";
}

function statusMessage(message) {
  return `<div class="status-message">${escapeHtml(message)}</div>`;
}

function setTabsEnabled(summaryReady, quizReady, analyticsReady) {
  if (summaryTabBtn) {
    summaryTabBtn.disabled = !summaryReady;
    summaryTabBtn.style.opacity = summaryReady ? "1" : "0.5";
  }
  if (quizTabBtn) {
    quizTabBtn.disabled = !quizReady;
    quizTabBtn.style.opacity = quizReady ? "1" : "0.5";
  }
  if (analyticsTabBtn) {
    analyticsTabBtn.disabled = !analyticsReady;
    analyticsTabBtn.style.opacity = analyticsReady ? "1" : "0.5";
  }
}

function clearAnalyticsPolling() {
  if (analyticsPollTimer) {
    clearTimeout(analyticsPollTimer);
    analyticsPollTimer = null;
  }
}

function currentStudentLink() {
  if (!currentTaskId) return "";
  return `/material/${encodeURIComponent(currentTaskId)}/`;
}

function renderReadyStatus(task) {
  const statusBits = [];
  if (fileToUpload?.name) statusBits.push(`Файл: ${escapeHtml(fileToUpload.name)}`);
  if (task?.status === "done") statusBits.push("Материалы готовы");
  const link = currentStudentLink();
  if (!link) {
    setStatus(statusBits.join(" · ") || "Обработка завершена");
    return;
  }
  setStatusHtml(`
    <div class="file-info">
      <span>${statusBits.join(" · ") || "Материалы готовы"}</span>
      <a href="${link}" target="_blank" rel="noopener noreferrer">Открыть ссылку ученика</a>
    </div>
  `);
}

function summarySectionsFromLines(summary) {
  const lines = Array.isArray(summary) ? summary : [];
  const sections = lines.map((item, index) => {
    const text = String(item || "").trim();
    const blockLines = text.split("\n");
    const heading = blockLines[0]?.trim() || "";
    const headingMatch = heading.match(/^#{1,6}\s+(.+)$/);
    const content = headingMatch
      ? blockLines.slice(1).join("\n").trim() || headingMatch[1].trim()
      : text;
    return {
      title: headingMatch ? headingMatch[1].trim() : `Раздел ${index + 1}`,
      content,
    };
  }).filter((item) => item.content);

  if (sections.length <= 5) return sections;

  const merged = [];
  const maxSections = 5;
  const baseSize = Math.floor(sections.length / maxSections);
  const extra = sections.length % maxSections;
  let offset = 0;

  for (let index = 0; index < maxSections; index += 1) {
    const groupSize = baseSize + (index < extra ? 1 : 0);
    const group = sections.slice(offset, offset + groupSize);
    offset += groupSize;
    if (!group.length) continue;

    merged.push({
      title: group[0].title,
      content: group.map((section, groupIndex) => {
        if (groupIndex === 0) return section.content;
        return `- **${section.title}**\n${section.content}`;
      }).join("\n\n"),
    });
  }

  return merged;
}

function cloneJson(data) {
  return JSON.parse(JSON.stringify(data));
}

function createEmptyQuestion() {
  return {
    question: "",
    options: ["", "", "", ""],
    correct_answer: 0,
  };
}

function createDefaultTest() {
  return {
    title: "Тест по занятию",
    questions: [createEmptyQuestion()],
  };
}

function normalizeQuizDraft(test) {
  const draft = cloneJson(test || createDefaultTest());
  draft.title = String(draft.title || "Тест по занятию");
  draft.questions = Array.isArray(draft.questions) && draft.questions.length
    ? draft.questions.map((question) => ({
      question: String(question?.question || ""),
      options: Array.isArray(question?.options)
        ? [...question.options.map((option) => String(option || "")), "", "", "", ""].slice(0, 4)
        : ["", "", "", ""],
      correct_answer: Number(question?.correct_answer || 0),
    }))
    : [createEmptyQuestion()];
  return draft;
}

function syncQuizEditorState(task) {
  const nextTaskId = String(task?.id || "");
  const fallbackTest = task?.test?.questions?.length ? task.test : createDefaultTest();
  if (nextTaskId !== quizEditorTaskId || !quizEditorState || !quizEditorDirty) {
    quizEditorState = normalizeQuizDraft(fallbackTest);
    quizEditorTaskId = nextTaskId;
    quizEditorDirty = false;
  }
}

async function saveQuizEditor() {
  if (!lastTask?.id || !quizEditorState) return;

  const payload = normalizeQuizDraft(quizEditorState);
  try {
    const task = await fetchJson(`/api/tasks/${encodeURIComponent(lastTask.id)}/test`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    quizEditorDirty = false;
    applyTaskState(task);
    setStatus("Тест сохранен и опубликован для студента.", false);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не удалось сохранить тест.";
    setStatus(message, false);
  }
}

function attachQuizEditorHandlers() {
  if (!quizEditorState) return;

  document.getElementById("quizTitleInput")?.addEventListener("input", (event) => {
    quizEditorState.title = event.target.value;
    quizEditorDirty = true;
  });

  document.querySelectorAll("[data-question-index]").forEach((node) => {
    const questionIndex = Number(node.getAttribute("data-question-index"));
    const field = node.getAttribute("data-field");
    const optionIndex = Number(node.getAttribute("data-option-index"));
    const action = node.getAttribute("data-action");

    if (action === "remove-question") {
      node.addEventListener("click", () => {
        quizEditorState.questions.splice(questionIndex, 1);
        if (!quizEditorState.questions.length) {
          quizEditorState.questions.push(createEmptyQuestion());
        }
        quizEditorDirty = true;
        renderQuiz(quizEditorState, lastTask?.quiz_text || "", lastTask?.status || "done");
      });
      return;
    }

    node.addEventListener("input", (event) => {
      if (!quizEditorState?.questions?.[questionIndex]) return;
      if (field === "question") {
        quizEditorState.questions[questionIndex].question = event.target.value;
        quizEditorDirty = true;
      } else if (field === "option") {
        quizEditorState.questions[questionIndex].options[optionIndex] = event.target.value;
        quizEditorDirty = true;
      }
    });

    node.addEventListener("change", (event) => {
      if (!quizEditorState?.questions?.[questionIndex]) return;
      if (field === "correct_answer") {
        quizEditorState.questions[questionIndex].correct_answer = Number(event.target.value);
        quizEditorDirty = true;
      }
    });
  });

  document.getElementById("quizAddQuestionBtn")?.addEventListener("click", () => {
    quizEditorState.questions.push(createEmptyQuestion());
    quizEditorDirty = true;
    renderQuiz(quizEditorState, lastTask?.quiz_text || "", lastTask?.status || "done");
  });

  document.getElementById("quizCancelEditBtn")?.addEventListener("click", () => {
    quizEditorState = normalizeQuizDraft(lastTask?.test || createDefaultTest());
    quizEditorDirty = false;
    renderQuiz(lastTask?.test || null, lastTask?.quiz_text || "", lastTask?.status || "");
  });

  document.getElementById("quizSaveEditBtn")?.addEventListener("click", () => {
    saveQuizEditor();
  });
}

function renderTranscript(task) {
  const transcript = Array.isArray(task?.transcript) ? task.transcript : [];
  if (!transcript.length && task?.status !== "processing") {
    transcriptContainer.innerHTML = statusMessage("Загрузите файл и нажмите «Обработать запись»");
    updateTranscriptJumpButton();
    return;
  }

  if (!transcript.length && task?.status === "processing") {
    transcriptContainer.innerHTML = `
      <div class="status-message">
        <span class="spinner-small"></span>
        Идёт распознавание записи...
      </div>
    `;
    updateTranscriptJumpButton();
    return;
  }

  const progress = Number(task?.progress || 0);
  const linesHtml = transcript.map((line) => `
    <div class="transcript-line">
      <div class="timestamp">${escapeHtml(formatTime(line.start))}</div>
      <div class="line-text">${escapeHtml(line.text || "")}</div>
    </div>
  `).join("");
  const statusHtml = task?.status === "processing"
    ? `<div class="status-message" style="margin-top: 16px;"><span class="spinner-small"></span> Обрабатываем запись <span class="progress-fixed">${Math.round(progress)}%</span></div>`
    : "";
  transcriptContainer.innerHTML = `${linesHtml}${statusHtml}`;
  requestAnimationFrame(updateTranscriptJumpButton);
}

function renderSummary(summary = [], status = "") {
  if (!summary.length && status !== "done") {
    summaryContainer.innerHTML = statusMessage(status === "processing" ? "Конспект формируется..." : "Конспект появится после обработки");
    return;
  }
  if (!summary.length) {
    summaryContainer.innerHTML = statusMessage("Конспект не был сформирован.");
    return;
  }

  const sections = summarySectionsFromLines(summary);
  const tocHtml = sections
    .map((section, index) => `<li class="toc-item"><a href="#summary-section-${index}">${escapeHtml(section.title)}</a></li>`)
    .join("");
  const contentHtml = sections
    .map((section, index) => `
      <section id="summary-section-${index}" class="summary-section">
        <h3>${escapeHtml(section.title)}</h3>
        <div class="content">${formatMarkdownToHtml(section.content)}</div>
      </section>
    `)
    .join("");

  summaryContainer.innerHTML = `
    <div class="summary-layout">
      <aside class="summary-toc">
        <h4>Оглавление</h4>
        <ul class="toc-list">${tocHtml}</ul>
      </aside>
      <div class="summary-content">${contentHtml}</div>
    </div>
  `;
  setTimeout(() => {
    renderMathInContainer(summaryContainer);
    highlightCodeInContainer(summaryContainer);
  }, 30);
}

function buildQuizEditorMarkup(test) {
  const draft = normalizeQuizDraft(test);
  const questionCards = draft.questions.map((question, questionIndex) => {
    const options = Array.isArray(question.options) ? question.options : ["", "", "", ""];
    const optionsHtml = options.map((option, optionIndex) => `
      <div class="quiz-edit-option">
        <div class="quiz-edit-option-label">${String.fromCharCode(65 + optionIndex)}</div>
        <input
          class="quiz-edit-input"
          type="text"
          value="${escapeHtml(option)}"
          data-question-index="${questionIndex}"
          data-field="option"
          data-option-index="${optionIndex}"
        >
      </div>
    `).join("");

    const correctOptions = options.map((_option, optionIndex) => `
      <option value="${optionIndex}" ${Number(question.correct_answer) === optionIndex ? "selected" : ""}>
        ${String.fromCharCode(65 + optionIndex)}
      </option>
    `).join("");

    return `
      <div class="quiz-edit-item">
        <div class="quiz-edit-top">
          <div class="quiz-edit-number">Вопрос ${questionIndex + 1}</div>
          <button class="quiz-edit-action-btn danger" type="button" data-question-index="${questionIndex}" data-action="remove-question">Удалить</button>
        </div>
        <div class="quiz-edit-label">Формулировка</div>
        <textarea class="quiz-edit-textarea" rows="3" data-question-index="${questionIndex}" data-field="question">${escapeHtml(question.question || "")}</textarea>
        <div class="quiz-edit-label">Варианты ответа</div>
        ${optionsHtml}
        <div class="quiz-edit-correct-row">
          <span>Правильный ответ</span>
          <select class="quiz-edit-select" data-question-index="${questionIndex}" data-field="correct_answer">
            ${correctOptions}
          </select>
        </div>
      </div>
    `;
  }).join("");

  return `
    <div class="quiz-results-card">
      <h3>Конструктор теста</h3>
      <p style="margin: 0 0 16px; color: rgba(29, 47, 64, 0.7);">
        Здесь редактируется та версия теста, которая уходит студенту по ссылке.
      </p>
      <div class="quiz-editor">
        <div class="quiz-edit-item">
          <div class="quiz-edit-label">Название теста</div>
          <input id="quizTitleInput" class="quiz-edit-input" type="text" value="${escapeHtml(draft.title || "")}">
        </div>
        ${questionCards}
        <div class="quiz-edit-global-actions">
          <div class="quiz-edit-global-add">
            <button id="quizAddQuestionBtn" class="quiz-edit-action-btn" type="button">Добавить вопрос</button>
          </div>
          <div class="quiz-edit-actions">
            <button id="quizCancelEditBtn" class="quiz-edit-action-btn" type="button">Сбросить изменения</button>
            <button id="quizSaveEditBtn" class="btn-secondary" type="button">Сохранить тест</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderStructuredQuizPreview(test) {
  const questions = Array.isArray(test?.questions) ? test.questions : [];
  if (!questions.length) return "";

  const itemsHtml = questions.map((item, index) => {
    const options = Array.isArray(item.options) ? item.options : [];
    const correctAnswer = Number(item.correct_answer || 0);
    const optionsHtml = options.map((option, optionIndex) => `
      <div class="quiz-option ${optionIndex === correctAnswer ? "correct-highlight" : ""}">
        <label>${escapeHtml(String.fromCharCode(65 + optionIndex))}) ${formatMarkdownToHtml(String(option || ""))}</label>
      </div>
    `).join("");

    return `
      <div class="quiz-item">
        <div class="quiz-question">${index + 1}. ${formatMarkdownToHtml(item.question || "")}</div>
        ${optionsHtml}
      </div>
    `;
  }).join("");

  return `
    <div class="quiz-results-card">
      <h3>${escapeHtml(test.title || "Тест")}</h3>
      <p style="margin: 0 0 16px; color: rgba(29, 47, 64, 0.7);">Так этот тест сейчас увидит ученик.</p>
      <div class="quiz-list">${itemsHtml}</div>
    </div>
  `;
}

function renderQuiz(test = null, quizText = "", status = "") {
  if (!quizText && !test && status !== "done") {
    quizContainer.innerHTML = statusMessage(status === "processing" ? "Тест формируется..." : "Тест появится после обработки");
    return;
  }
  if (!quizText && !test) {
    quizContainer.innerHTML = statusMessage("Тест не был сформирован.");
    return;
  }

  const draft = quizEditorState || normalizeQuizDraft(test || createDefaultTest());
  quizEditorState = draft;

  if (test?.questions?.length) {
    quizContainer.innerHTML = `
      ${renderStructuredQuizPreview(test)}
      ${buildQuizEditorMarkup(draft)}
    `;
    attachQuizEditorHandlers();
    setTimeout(() => {
      renderMathInContainer(quizContainer);
      highlightCodeInContainer(quizContainer);
    }, 30);
    return;
  }

  quizContainer.innerHTML = `
    <div class="quiz-results-card">
      <h3>Черновик теста от LLM</h3>
      <div class="quiz-markdown">${formatMarkdownToHtml(quizText)}</div>
    </div>
    ${buildQuizEditorMarkup(draft)}
  `;
  attachQuizEditorHandlers();
  setTimeout(() => {
    renderMathInContainer(quizContainer);
    highlightCodeInContainer(quizContainer);
  }, 30);
}

function renderAnalytics(task) {
  const analytics = task?.analytics || null;
  const status = task?.status || "";
  const jobStatus = task?.analytics_job_status || "";
  const jobError = task?.analytics_job_error;
  const transcriptReady = Array.isArray(task?.transcript) && task.transcript.length > 0;

  if (!analytics && !transcriptReady && !["queued", "processing", "failed"].includes(jobStatus)) {
    analyticsContainer.innerHTML = statusMessage("Аналитика станет доступна после появления транскрипта занятия");
    return;
  }

  if (!analytics && ["queued", "processing"].includes(jobStatus)) {
    analyticsContainer.innerHTML = `
      <div class="analytics-card">
        <div class="analytics-title">Анализ запущен</div>
        <div class="status-message">
          <span class="spinner-small"></span>
          Анализируем транскрипт преподавателя. Статус: ${escapeHtml(jobStatus)}.
        </div>
      </div>
    `;
    attachAnalyticsActionHandlers(task);
    return;
  }

  if (!analytics) {
    const detail = jobError?.detail ? `<div class="status-message">${escapeHtml(jobError.detail)}</div>` : "";
    analyticsContainer.innerHTML = `
      <div class="analytics-card">
        <div class="analytics-title">Аналитика занятия</div>
        <div class="analytics-subtopic">Анализ больше не строится внутри общего пайплайна и запускается отдельным запросом.</div>
        ${detail}
        <div style="margin-top: 16px;">
          <button id="startAnalyticsBtn" class="btn-secondary" type="button">
            ${jobStatus === "failed" ? "Запустить повторно" : "Запустить аналитику"}
          </button>
        </div>
      </div>
    `;
    attachAnalyticsActionHandlers(task);
    return;
  }

  const metrics = Array.isArray(analytics.metrics) ? analytics.metrics : [];
  const recommendations = Array.isArray(analytics.recommendations) ? analytics.recommendations : [];
  const supportingFragments = Array.isArray(analytics.supporting_fragments) ? analytics.supporting_fragments : [];
  const dialogueAnalysis = analytics.dialogue_analysis && typeof analytics.dialogue_analysis === "object" ? analytics.dialogue_analysis : {};
  const totalScore = Number(analytics.total_score || 0);
  const maxScore = Number(analytics.max_score || 0);
  const percent = maxScore > 0 ? Math.max(0, Math.min(100, Math.round((totalScore / maxScore) * 100))) : 0;

  const metricsHtml = metrics.map((metric) => {
    const score = Number(metric.score || 0);
    const max = Number(metric.max_score || 1);
    const metricPercent = Math.max(0, Math.min(100, Math.round((score / max) * 100)));
    const level = metricPercent >= 80 ? "good" : metricPercent >= 50 ? "medium" : "low";
    const evidence = Array.isArray(metric.evidence) ? metric.evidence : [];
    const signals = Array.isArray(metric.signals) ? metric.signals : [];
    const evidenceHtml = evidence.length
      ? `<div class="analytics-meta">Фрагменты: ${evidence.map((item) => escapeHtml(String(item))).join(" · ")}</div>`
      : "";
    const signalsHtml = signals.length
      ? `<div class="analytics-meta">${signals.map((item) => escapeHtml(String(item))).join(" · ")}</div>`
      : "";

    return `
      <div class="analytics-card">
        <div class="analytics-title">${escapeHtml(metric.title || metric.id || "Метрика")}</div>
        <div class="analytics-row">
          <div class="analytics-subtopic">${escapeHtml(metric.comment || "")}</div>
          <div class="analytics-progress-line">
            <div class="analytics-progress-fill ${level}" style="width: ${metricPercent}%"></div>
          </div>
          <div class="analytics-percent">${score}/${max}</div>
        </div>
        ${signalsHtml}
        ${evidenceHtml}
      </div>
    `;
  }).join("");

  const recommendationsHtml = recommendations.length
    ? recommendations.map((item) => `<div class="analytics-reco medium">${escapeHtml(String(item))}</div>`).join("")
    : `<div class="analytics-reco muted">Рекомендации не сформированы.</div>`;

  const dialogueSignals = [];
  if (dialogueAnalysis.lesson_format?.label) {
    dialogueSignals.push(`Формат: ${String(dialogueAnalysis.lesson_format.label)}`);
  }
  if (Array.isArray(dialogueAnalysis.question_types?.signals)) {
    dialogueSignals.push(...dialogueAnalysis.question_types.signals.map((item) => String(item)));
  }
  if (Array.isArray(dialogueAnalysis.roles?.detected_roles) && dialogueAnalysis.roles.detected_roles.length) {
    dialogueSignals.push(`Роли: ${dialogueAnalysis.roles.detected_roles.join(", ")}`);
  }
  if (Array.isArray(dialogueAnalysis.segmentation?.signals)) {
    dialogueSignals.push(...dialogueAnalysis.segmentation.signals.map((item) => String(item)));
  }

  const dialogueCardHtml = dialogueSignals.length
    ? `
      <div class="analytics-card">
        <div class="analytics-title">Структура диалога</div>
        <div class="analytics-reco-list">
          ${dialogueSignals.slice(0, 8).map((item) => `<div class="analytics-reco medium">${escapeHtml(item)}</div>`).join("")}
        </div>
      </div>
    `
    : "";

  const supportingFragmentsHtml = supportingFragments.length
    ? `
      <div class="analytics-card">
        <div class="analytics-title">Подтверждающие фрагменты</div>
        <div class="analytics-reco-list">
          ${supportingFragments.slice(0, 8).map((item) => `<div class="analytics-reco medium">${escapeHtml(String(item))}</div>`).join("")}
        </div>
      </div>
    `
    : "";

  analyticsContainer.innerHTML = `
    <div class="analytics-stack">
      <div class="analytics-card">
        <div class="analytics-title">Управление анализом</div>
        <div class="analytics-subtopic">Аналитика вызывается отдельным запросом по готовому транскрипту.</div>
        <div style="margin-top: 16px;">
          <button id="startAnalyticsBtn" class="btn-secondary" type="button">Пересчитать аналитику</button>
        </div>
      </div>
      <div class="analytics-card">
        <div class="analytics-title">Итоговая оценка занятия</div>
        <div class="analytics-row">
          <div class="analytics-subtopic">Суммарный балл по рубрике</div>
          <div class="analytics-progress-line">
            <div class="analytics-progress-fill ${percent >= 80 ? "good" : percent >= 50 ? "medium" : "low"}" style="width: ${percent}%"></div>
          </div>
          <div class="analytics-percent">${totalScore}/${maxScore}</div>
        </div>
        <div class="analytics-meta">Источник: ${escapeHtml(analytics.source || "analysis")}</div>
      </div>
      ${metricsHtml}
      ${dialogueCardHtml}
      ${supportingFragmentsHtml}
      <div class="analytics-card">
        <div class="analytics-title">Рекомендации</div>
        <div class="analytics-reco-list">${recommendationsHtml}</div>
      </div>
    </div>
  `;
  attachAnalyticsActionHandlers(task);
}

function updateActiveTabContent(task) {
  const active = document.querySelector(".tab-btn.active");
  if (!active) return;
  const tab = active.getAttribute("data-tab");
  if (tab === "transcript") renderTranscript(task);
  if (tab === "summary") renderSummary(task?.summary || [], task?.status || "");
  if (tab === "quiz") renderQuiz(task?.test || null, task?.quiz_text || "", task?.status || "");
  if (tab === "analytics") renderAnalytics(task);
}

function applyTaskState(task) {
  if (!task) return;
  lastTask = task;
  if (task.id) currentTaskId = task.id;
  if (task.status === "done") {
    streamCompletedSuccessfully = true;
  }
  const summaryReady = Array.isArray(task.summary) && task.summary.length > 0;
  const quizReady = Boolean(task.quiz_text || task.test);
  const transcriptReady = Array.isArray(task.transcript) && task.transcript.length > 0;
  const analyticsReady = transcriptReady || Boolean(task.analytics) || task.status === "done" || ["queued", "processing", "failed"].includes(task.analytics_job_status || "");
  if (quizReady) {
    syncQuizEditorState(task);
  } else {
    quizEditorState = null;
    quizEditorDirty = false;
    quizEditorTaskId = String(task.id || "");
  }
  setTabsEnabled(summaryReady, quizReady, analyticsReady);
  if (editQuizBtn) {
    editQuizBtn.hidden = true;
    if (editQuizBtn.parentElement) editQuizBtn.parentElement.hidden = true;
  }
  updateActiveTabContent(task);
  maybeResumeAnalyticsPolling(task);

  if (task.status === "done") {
    renderReadyStatus(task);
  } else if (task.status === "processing") {
    setStatus("Обрабатываем запись...", true);
  } else if (task.status === "failed") {
    setStatus(getErrorMessage(task), false);
  }
}

function stopProcessingUi(message) {
  if (streamCompletedSuccessfully || lastTask?.status === "done") {
    renderReadyStatus(lastTask);
    return;
  }
  streamFailed = true;
  streamActive = false;
  generateBtn.disabled = false;
  generateBtn.textContent = "Обработать запись";
  setStatus(message, false);
  transcriptContainer.innerHTML = statusMessage(message);
  summaryContainer.innerHTML = statusMessage(message);
  quizContainer.innerHTML = statusMessage(message);
  analyticsContainer.innerHTML = statusMessage(message);
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    cache: "no-store",
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

async function startAnalyticsJob() {
  if (!lastTask?.id) return;
  clearAnalyticsPolling();
  try {
    const response = await fetchJson(`/api/tasks/${encodeURIComponent(lastTask.id)}/analytics`, {
      method: "POST",
    });
    if (response.task) {
      applyTaskState(response.task);
    }
    if (response.job_id) {
      scheduleAnalyticsPoll(response.job_id);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не удалось запустить аналитику.";
    analyticsContainer.innerHTML = statusMessage(message);
  }
}

function scheduleAnalyticsPoll(jobId) {
  if (!lastTask?.id || !jobId) return;
  clearAnalyticsPolling();
  analyticsPollTimer = setTimeout(() => {
    pollAnalyticsJob(jobId);
  }, 2000);
}

async function pollAnalyticsJob(jobId) {
  if (!lastTask?.id || !jobId) return;
  try {
    const response = await fetchJson(`/api/tasks/${encodeURIComponent(lastTask.id)}/analytics/${encodeURIComponent(jobId)}`);
    if (response.task) {
      applyTaskState(response.task);
    }
    if (["queued", "processing"].includes(response.status)) {
      scheduleAnalyticsPoll(jobId);
    } else {
      clearAnalyticsPolling();
    }
  } catch (error) {
    clearAnalyticsPolling();
    const message = error instanceof Error ? error.message : "Не удалось получить статус аналитики.";
    if (lastTask) {
      renderAnalytics({
        ...lastTask,
        analytics_job_status: "failed",
        analytics_job_error: { detail: message },
      });
    } else {
      analyticsContainer.innerHTML = statusMessage(message);
    }
  }
}

function maybeResumeAnalyticsPolling(task) {
  if (!task?.id || !task?.analytics_job_id) return;
  if (!["queued", "processing"].includes(task.analytics_job_status || "")) {
    clearAnalyticsPolling();
    return;
  }
  if (analyticsPollTimer) return;
  scheduleAnalyticsPoll(task.analytics_job_id);
}

function attachAnalyticsActionHandlers(task) {
  document.getElementById("startAnalyticsBtn")?.addEventListener("click", () => {
    if (!task?.id) return;
    startAnalyticsJob();
  });
}

async function loadTask(taskId) {
  const task = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`);
  applyTaskState(task);
}

async function streamFileToWs(file) {
  if (streamWS && streamWS.readyState === WebSocket.OPEN) {
    streamWS.close();
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  streamWS = new WebSocket(`${proto}://${location.host}/ws/stream`);
  streamWS.binaryType = "arraybuffer";

  streamWS.onopen = async () => {
    streamActive = true;
    streamFailed = false;
    streamCompletedSuccessfully = false;
    initAcked = false;
    lastTask = null;
    currentTaskId = "";
    setTabsEnabled(false, false, false);
    setStatus("Инициализация стрима...", true);
    generateBtn.disabled = true;
    generateBtn.textContent = "Обработка...";

    if (!file || file.size === 0) {
      setStatus("Файл не выбран или пустой", false);
      streamActive = false;
      return;
    }

    streamWS.send(JSON.stringify({
      type: "init",
      config: { language: null },
      filename: file.name,
      content_type: file.type || "application/octet-stream",
    }));
  };

  streamWS.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "error") {
        const message = getErrorMessage(msg);
        stopProcessingUi(message);
        if (streamWS && streamWS.readyState === WebSocket.OPEN) {
          streamWS.close();
        }
        return;
      }

      if (msg.type === "init_ack") {
        if (initAcked) return;
        initAcked = true;
        currentTaskId = msg.task_id || "";
        setStatus("Стриминг начат...", true);
        (async () => {
          const chunkSize = 64 * 1024;
          let offset = 0;
          while (offset < file.size && streamActive) {
            const slice = file.slice(offset, offset + chunkSize);
            const buffer = await slice.arrayBuffer();
            streamWS.send(buffer);
            offset += chunkSize;
            await new Promise((resolve) => setTimeout(resolve, 20));
          }
          if (streamActive) {
            streamWS.send(JSON.stringify({ type: "end" }));
          }
        })();
        return;
      }

      if (msg.type === "pong") return;

      const task = lastTask || {
        id: currentTaskId,
        transcript: [],
        summary: [],
        test: null,
        quiz_text: "",
        analytics: null,
        analytics_job_id: null,
        analytics_job_status: null,
        analytics_job_error: null,
        status: "processing",
        progress: 0,
      };

      if (msg.type === "summary") {
        task.summary = Array.isArray(msg.summary)
          ? msg.summary
          : (msg.text ? msg.text.split("\n\n").map((block) => block.trim()).filter(Boolean) : []);
        task.status = "processing";
        applyTaskState(task);
        return;
      }

      if (msg.type === "quiz_text") {
        task.test = msg.test || null;
        task.quiz_text = msg.text || "";
        task.status = "done";
        applyTaskState(task);
        return;
      }

      if (msg.type === "analytics") {
        task.analytics = msg.analytics || null;
        task.analytics_job_status = "completed";
        task.analytics_job_error = null;
        task.status = "done";
        applyTaskState(task);
        return;
      }

      if (msg.text && msg.is_final && !String(msg.text).startsWith("[partial]")) {
        task.transcript = task.transcript || [];
        task.transcript.push({
          start: Math.floor((msg.start || 0) / 1000),
          text: msg.text || "",
        });
      }
      task.progress = Math.max(Number(task.progress || 0), 50);
      task.status = "processing";
      applyTaskState(task);
    } catch (_e) {
      // ignore malformed payloads
    }
  };

  streamWS.onclose = () => {
    streamActive = false;
    generateBtn.disabled = false;
    generateBtn.textContent = "Обработать запись";
    if (streamCompletedSuccessfully || (!streamFailed && lastTask?.status === "done")) {
      renderReadyStatus(lastTask);
    } else if (!streamFailed) {
      setStatus("Стриминг завершен", false);
    }
  };

  streamWS.onerror = () => {
    if (streamCompletedSuccessfully || lastTask?.status === "done") {
      return;
    }
    stopProcessingUi("Ошибка соединения со стримингом.");
  };
}

function resetUpload() {
  fileToUpload = null;
  lastTask = null;
  currentTaskId = "";
  streamFailed = false;
  streamCompletedSuccessfully = false;
  clearAnalyticsPolling();
  quizEditorState = null;
  quizEditorDirty = false;
  quizEditorTaskId = "";
  if (fileInput) fileInput.value = "";
  dropZone.classList.remove("drag-over");
  generateBtn.disabled = true;
  generateBtn.textContent = "Обработать запись";
  setTabsEnabled(false, false, false);
  setStatus("");
  transcriptContainer.innerHTML = statusMessage("Загрузите файл и нажмите «Обработать запись»");
  summaryContainer.innerHTML = statusMessage("Конспект появится после обработки");
  quizContainer.innerHTML = statusMessage("Тест появится после обработки");
  analyticsContainer.innerHTML = statusMessage("Аналитика появится после обработки");
  updateTranscriptJumpButton();
  if (editQuizBtn) {
    editQuizBtn.hidden = true;
    if (editQuizBtn.parentElement) editQuizBtn.parentElement.hidden = true;
  }
}

function handleFile(file) {
  if (!file) return;
  fileToUpload = file;
  setStatus(`Файл готов: ${escapeHtml(file.name)}`);
  generateBtn.disabled = false;
}

function updateTranscriptJumpButton() {
  if (!transcriptContainer || !transcriptJumpBtn) return;
  const hasOverflow = transcriptContainer.scrollHeight > transcriptContainer.clientHeight + 2;
  const distanceFromBottom = transcriptContainer.scrollHeight - transcriptContainer.scrollTop - transcriptContainer.clientHeight;
  transcriptJumpBtn.hidden = !(hasOverflow && distanceFromBottom > 20);
}

function scrollTranscriptToBottom() {
  transcriptContainer.scrollTo({ top: transcriptContainer.scrollHeight, behavior: "smooth" });
}

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const files = e.dataTransfer.files;
  if (files.length > 0) handleFile(files[0]);
});
fileInput.addEventListener("change", (e) => {
  if (e.target.files.length > 0) handleFile(e.target.files[0]);
});
generateBtn.addEventListener("click", async () => {
  if (!fileToUpload) {
    alert("Сначала выберите файл");
    return;
  }
  await streamFileToWs(fileToUpload);
});
resetUploadBtn.addEventListener("click", resetUpload);
transcriptContainer.addEventListener("scroll", updateTranscriptJumpButton);
transcriptJumpBtn?.addEventListener("click", scrollTranscriptToBottom);

tabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    const tabId = btn.getAttribute("data-tab");
    tabBtns.forEach((item) => item.classList.remove("active"));
    btn.classList.add("active");
    Object.values(panels).forEach((panel) => panel.classList.remove("active-pane"));
    panels[tabId]?.classList.add("active-pane");
    if (lastTask) updateActiveTabContent(lastTask);
  });
});

if (editSummaryBtn) editSummaryBtn.hidden = true;
if (editQuizBtn) {
  editQuizBtn.hidden = true;
  if (editQuizBtn.parentElement) editQuizBtn.parentElement.hidden = true;
}
if (historyToggleBtn) historyToggleBtn.hidden = true;
if (historySidebar) historySidebar.hidden = true;
if (historyDrawer) historyDrawer.hidden = true;
if (historyOverlay) historyOverlay.hidden = true;
if (historyList) historyList.innerHTML = "";
if (historyListMobile) historyListMobile.innerHTML = "";
if (closeHistoryBtn) {
  closeHistoryBtn.addEventListener("click", () => {
    if (historyDrawer) historyDrawer.hidden = true;
    if (historyOverlay) historyOverlay.hidden = true;
  });
}

if (requestedTaskId) {
  loadTask(requestedTaskId).catch(() => {
    setStatus("Не удалось загрузить задачу из URL.", false);
  });
} else {
  resetUpload();
}
