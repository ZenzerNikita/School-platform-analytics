let fileToUpload = null;
let streamWS = null;
let streamActive = false;
let lastTask = null;
let quizState = { selectedIndex: null, checked: false };
let summaryEditable = false;
let initAcked = false;
let streamFailed = false;

const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadStatusDiv = document.getElementById("uploadStatus");
const generateBtn = document.getElementById("generateBtn");
const resetUploadBtn = document.getElementById("resetUploadBtn");
const transcriptContainer = document.getElementById("transcriptContainer");
const summaryContainer = document.getElementById("summaryContainer");
const quizContainer = document.getElementById("quizContainer");
const tabBtns = document.querySelectorAll(".tab-btn");
const panelTranscript = document.getElementById("panelTranscript");
const panelSummary = document.getElementById("panelSummary");
const panelQuiz = document.getElementById("panelQuiz");
const panelAnalytics = document.getElementById("panelAnalytics");
const summaryTabBtn = document.getElementById("summaryTabBtn");
const quizTabBtn = document.getElementById("quizTabBtn");
const analyticsTabBtn = document.getElementById("analyticsTabBtn");
const editSummaryBtn = document.getElementById("editSummaryBtn");
const quizSuggested = document.getElementById("quizSuggested");
const qText = document.getElementById("qText");
const qA = document.getElementById("qA");
const qB = document.getElementById("qB");
const qC = document.getElementById("qC");
const qD = document.getElementById("qD");
const addQuestionBtn = document.getElementById("addQuestionBtn");
const quizList = document.getElementById("quizList");

const manualQuestions = [];

const ERROR_FALLBACKS = {
  400: "Не удалось обработать файл. Проверьте формат записи и попробуйте снова.",
  500: "Ошибка обработки на сервере. Попробуйте повторить позже.",
  503: "ML-сервис временно недоступен. Попробуйте позже.",
};

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>]/g, function (m) {
    if (m === "&") return "&amp;";
    if (m === "<") return "&lt;";
    if (m === ">") return "&gt;";
    return m;
  });
}

function formatTime(sec) {
  const m = Math.floor(sec / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(sec % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

function setStatus(message, loading = false) {
  if (!message) {
    uploadStatusDiv.innerHTML = "";
    return;
  }
  uploadStatusDiv.innerHTML = `
    <div class="file-info">
      ${loading ? '<span class="loader"></span>' : ""}
      <span>${message}</span>
    </div>
  `;
}

function getErrorMessage(msg) {
  const statusCode = Number(msg?.status_code || 0);
  if (msg?.detail) {
    return `${msg.detail}${statusCode ? ` (${statusCode})` : ""}`;
  }
  if (ERROR_FALLBACKS[statusCode]) {
    return ERROR_FALLBACKS[statusCode];
  }
  return "Не удалось завершить обработку файла.";
}

function renderErrorState(message) {
  const safeMessage = escapeHtml(message);
  transcriptContainer.innerHTML = `<div class="status-message">${safeMessage}</div>`;
  summaryContainer.innerHTML = `<div class="status-message">${safeMessage}</div>`;
  if (quizContainer) {
    quizContainer.innerHTML = `<div class="status-message">${safeMessage}</div>`;
  }
  if (panelAnalytics) {
    panelAnalytics.innerHTML = `<div class="status-message">${safeMessage}</div>`;
  }
}

function stopProcessingUi(message) {
  streamFailed = true;
  streamActive = false;
  generateBtn.disabled = false;
  generateBtn.textContent = "Обработать запись";
  setStatus(message, false);
  renderErrorState(message);
}

function setTabsEnabled(summaryReady, quizReady, analyticsReady = false) {
  if (summaryReady && summaryTabBtn) {
    summaryTabBtn.disabled = false;
    summaryTabBtn.style.opacity = "1";
  }
  if (quizReady && quizTabBtn) {
    quizTabBtn.disabled = false;
    quizTabBtn.style.opacity = "1";
  }
  if (analyticsReady && analyticsTabBtn) {
    analyticsTabBtn.disabled = false;
    analyticsTabBtn.style.opacity = "1";
  }
}

function renderTranscript(transcript = [], isGenerating = false) {
  if (!transcriptContainer) return;
  if (transcript.length === 0) {
    transcriptContainer.innerHTML = `<div class="status-message">Транскрипт появится после начала генерации</div>`;
    return;
  }
  let html = "";
  for (let line of transcript) {
    html += `
      <div class="transcript-line">
        <div class="timestamp">${escapeHtml(formatTime(line.start))}</div>
        <div class="line-text editable" contenteditable="true">${escapeHtml(line.text)}</div>
      </div>
    `;
  }
  if (isGenerating) {
    html += `<div class="transcript-line" style="opacity:0.7;"><div class="timestamp"></div><div class="line-text"><span class="spinner-small" style="display:inline-block; vertical-align:middle;"></span> распознавание...</div></div>`;
  }
  transcriptContainer.innerHTML = html;
}

function renderSummary(summary = [], status = "") {
  if (!summaryContainer) return;
  if ((!summary || summary.length === 0) && status !== "done") {
    summaryContainer.innerHTML = `<div class="status-message">Конспект появится после генерации</div>`;
    return;
  }
  const text = summary.length
    ? summary.map((item) => `• ${item}`).join("\n")
    : "Заглушка: конспект сформирован.";
  summaryContainer.innerHTML = `<div class="summary-text summary-editable editable" contenteditable="${summaryEditable}">${escapeHtml(text)}</div>`;
}

function renderQuiz(test, status = "") {
  if (!quizContainer) return;
  if (!test && status !== "done") {
    quizContainer.innerHTML = `<div class="status-message">Тест появится после генерации конспекта</div>`;
    return;
  }
  const quiz = test || {
    question: "Пример вопроса",
    options: ["A", "B", "C", "D"],
    answer: "B",
  };
  let html = `<div class="questions-block">`;
  html += `
    <div class="quiz-item">
      <div class="quiz-question">${escapeHtml(quiz.question)}</div>
  `;
  const options = quiz.options || [];
  for (let i = 0; i < options.length; i++) {
    const opt = options[i];
    const isSelected = quizState.selectedIndex === i;
    const isCorrect = quizState.checked && opt === quiz.answer;
    const isWrong = quizState.checked && isSelected && opt !== quiz.answer;
    html += `
      <label class="quiz-option ${isCorrect ? "option-correct" : ""} ${isWrong ? "option-wrong" : ""}">
        <input type="radio" name="quiz" value="${i}" ${isSelected ? "checked" : ""} />
        <span>${escapeHtml(opt)}</span>
        ${isCorrect ? '<span class="answer-tag">правильный</span>' : ""}
        ${isWrong ? '<span class="answer-tag" style="color:#b91c1c;">неверный</span>' : ""}
      </label>
    `;
  }
  html += `
      <div class="quiz-actions">
        <button id="checkQuizBtn" class="btn-secondary">Проверить</button>
        <button id="resetQuizBtn" class="btn-secondary">Сбросить</button>
      </div>
    </div>
  `;
  html += `</div>`;
  quizContainer.innerHTML = html;

  const checkBtn = document.getElementById("checkQuizBtn");
  const resetBtn = document.getElementById("resetQuizBtn");
  const radios = quizContainer.querySelectorAll('input[name="quiz"]');
  radios.forEach((r) =>
    r.addEventListener("change", (e) => {
      quizState.selectedIndex = Number(e.target.value);
    }),
  );
  checkBtn.addEventListener("click", () => {
    quizState.checked = true;
    renderQuiz(quiz, status);
  });
  resetBtn.addEventListener("click", () => {
    quizState.selectedIndex = null;
    quizState.checked = false;
    renderQuiz(quiz, status);
  });
}

function renderAnalytics(analytics, status = "") {
  if (!panelAnalytics) return;
  if (!analytics && status !== "done") {
    panelAnalytics.innerHTML = `<div class="status-message">Аналитика появится после обработки занятия</div>`;
    return;
  }
  if (!analytics) {
    panelAnalytics.innerHTML = `<div class="status-message">Аналитика пока не сформирована</div>`;
    return;
  }

  const metrics = Array.isArray(analytics.metrics) ? analytics.metrics : [];
  const recommendations = Array.isArray(analytics.recommendations)
    ? analytics.recommendations
    : [];
  const supportingFragments = Array.isArray(analytics.supporting_fragments)
    ? analytics.supporting_fragments
    : [];
  const dialogueAnalysis = analytics.dialogue_analysis && typeof analytics.dialogue_analysis === "object"
    ? analytics.dialogue_analysis
    : {};
  const totalScore = Number(analytics.total_score || 0);
  const maxScore = Number(analytics.max_score || 0);
  const percent = maxScore > 0
    ? Math.max(0, Math.min(100, Math.round((totalScore / maxScore) * 100)))
    : 0;

  const metricsHtml = metrics
    .map((metric) => {
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
    })
    .join("");

  const recommendationsHtml = recommendations.length
    ? recommendations
        .map((item) => `<div class="analytics-reco medium">${escapeHtml(String(item))}</div>`)
        .join("")
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
          ${dialogueSignals
            .slice(0, 8)
            .map((item) => `<div class="analytics-reco medium">${escapeHtml(item)}</div>`)
            .join("")}
        </div>
      </div>
    `
    : "";

  const supportingFragmentsHtml = supportingFragments.length
    ? `
      <div class="analytics-card">
        <div class="analytics-title">Подтверждающие фрагменты</div>
        <div class="analytics-reco-list">
          ${supportingFragments
            .slice(0, 8)
            .map((item) => `<div class="analytics-reco medium">${escapeHtml(String(item))}</div>`)
            .join("")}
        </div>
      </div>
    `
    : "";

  panelAnalytics.innerHTML = `
    <div class="analytics-stack">
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
}

function renderManualQuiz() {
  if (!quizList) return;
  if (manualQuestions.length === 0) {
    quizList.innerHTML = `<div class="status-message">Пока нет добавленных вопросов</div>`;
    return;
  }
  quizList.innerHTML = manualQuestions
    .map(
      (q, idx) => `
        <div class="quiz-item-card">
          <strong>${idx + 1}. ${escapeHtml(q.question)}</strong>
          <div>A) ${escapeHtml(q.options[0])}</div>
          <div>B) ${escapeHtml(q.options[1])}</div>
          <div>C) ${escapeHtml(q.options[2])}</div>
          <div>D) ${escapeHtml(q.options[3])}</div>
          <div class="muted">Правильный: ${q.correct}</div>
        </div>
      `,
    )
    .join("");
}

function updateActiveTabContent(task) {
  const active = document.querySelector(".tab-btn.active");
  if (!active) return;
  const tab = active.getAttribute("data-tab");
  if (tab === "transcript") {
    renderTranscript(task?.transcript || [], task?.status === "processing");
  }
  if (tab === "summary") {
    renderSummary(task?.summary || [], task?.status || "");
  }
  if (tab === "quiz") {
    renderQuiz(task?.test, task?.status || "");
  }
  if (tab === "analytics") {
    renderAnalytics(task?.analytics, task?.status || "");
  }
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
    initAcked = false;
    setStatus("Инициализация стрима...", true);
    generateBtn.disabled = true;
    generateBtn.textContent = "Обработка...";

    if (!file || file.size === 0) {
      setStatus("Файл не выбран или пустой", false);
      streamActive = false;
      return;
    }

    const initPayload = {
      type: "init",
      config: { language: null },
      filename: file.name,
      content_type: file.type || "application/octet-stream",
    };
    streamWS.send(JSON.stringify(initPayload));
    console.log("ws_stream: init sent");
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
        console.log("ws_stream: init_ack received");
        setStatus("Стриминг начат", true);
        (async () => {
          const chunkSize = 64 * 1024;
          let offset = 0;
          while (offset < file.size && streamActive) {
            const slice = file.slice(offset, offset + chunkSize);
            const buffer = await slice.arrayBuffer();
            streamWS.send(buffer);
            console.log("ws_stream: sent chunk", buffer.byteLength);
            offset += chunkSize;
            await new Promise((r) => setTimeout(r, 20));
          }
          if (streamActive) {
            streamWS.send(JSON.stringify({ type: "end" }));
            console.log("ws_stream: end sent");
          }
        })();
        return;
      }
      if (msg.type === "pong") {
        return;
      }
      const task = lastTask || {
        transcript: [],
        summary: [],
        test: null,
        analytics: null,
        status: "processing",
        progress: 0,
      };
      if (msg.type === "summary") {
        task.summary = msg.text ? msg.text.split("\n").filter(Boolean) : [];
        setTabsEnabled(true, task.test != null, task.analytics != null);
        lastTask = task;
        updateActiveTabContent(task);
        return;
      }
      if (msg.type === "quiz_text") {
        if (quizSuggested) {
          quizSuggested.textContent = msg.text || "";
        }
        task.quiz_text = msg.text || "";
        task.status = "done";
        setTabsEnabled(task.summary != null, true, task.analytics != null);
        lastTask = task;
        return;
      }
      if (msg.type === "analytics") {
        task.analytics = msg.analytics || null;
        task.status = "done";
        setTabsEnabled(task.summary != null, true, true);
        lastTask = task;
        updateActiveTabContent(task);
        return;
      }
      if (!msg.is_final || (msg.text || "").startsWith("[partial]")) {
        task.status = "processing";
      }
      task.transcript = task.transcript || [];
      if (msg.text) {
        task.transcript.push({
          start: Math.floor((msg.start || 0) / 1000),
          text: msg.text || "",
        });
      }
      lastTask = task;
      updateActiveTabContent(task);
    } catch (e) {
      console.error(e);
    }
  };

  streamWS.onclose = () => {
    streamActive = false;
    generateBtn.disabled = false;
    generateBtn.textContent = "Обработать запись";
    if (!streamFailed) {
      setStatus("Стриминг завершен", false);
    }
  };

  streamWS.onerror = () => {
    stopProcessingUi("Ошибка соединения со стримингом.");
  };
}

function resetUpload() {
  fileToUpload = null;
  quizState = { selectedIndex: null, checked: false };
  summaryEditable = false;
  if (fileInput) fileInput.value = "";
  dropZone.classList.remove("drag-over");
  generateBtn.disabled = true;
  generateBtn.textContent = "Обработать запись";
  lastTask = null;
  streamFailed = false;
  if (summaryTabBtn) {
    summaryTabBtn.disabled = true;
    summaryTabBtn.style.opacity = "0.5";
  }
  if (quizTabBtn) {
    quizTabBtn.disabled = true;
    quizTabBtn.style.opacity = "0.5";
  }
  if (analyticsTabBtn) {
    analyticsTabBtn.disabled = true;
    analyticsTabBtn.style.opacity = "0.5";
  }
  setStatus("");
  transcriptContainer.innerHTML = `<div class="status-message">Загрузите запись и нажмите «Обработать запись»</div>`;
  summaryContainer.innerHTML = `<div class="status-message">Конспект появится после генерации</div>`;
  if (quizContainer) {
    quizContainer.innerHTML = `<div class="status-message">Тест появится после генерации конспекта</div>`;
  }
  if (quizSuggested) {
    quizSuggested.textContent = "Здесь появится предложенный тест...";
  }
  if (panelAnalytics) {
    panelAnalytics.innerHTML = `<div class="status-message">Аналитика появится после обработки занятия</div>`;
  }
}

function handleFile(file) {
  if (file) {
    fileToUpload = file;
    setStatus(`Файл готов: ${escapeHtml(file.name)}`);
    generateBtn.disabled = false;
  }
}

dropZone.addEventListener("click", () => {
  fileInput.click();
});

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});

dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const files = e.dataTransfer.files;
  if (files.length > 0) {
    handleFile(files[0]);
  }
});

fileInput.addEventListener("change", (e) => {
  if (e.target.files.length > 0) {
    handleFile(e.target.files[0]);
  }
});

generateBtn.addEventListener("click", async () => {
  if (!fileToUpload) {
    alert("Сначала выберите аудиофайл");
    return;
  }
  await streamFileToWs(fileToUpload);
});

resetUploadBtn.addEventListener("click", resetUpload);

if (addQuestionBtn) {
  addQuestionBtn.addEventListener("click", (e) => {
    e.preventDefault();
    const question = qText?.value?.trim();
    const options = [qA?.value, qB?.value, qC?.value, qD?.value].map((v) =>
      (v || "").trim(),
    );
    const correct = document.querySelector('input[name="correct"]:checked');
    if (!question || options.some((o) => !o) || !correct) {
      alert("Заполни вопрос, все варианты и правильный ответ");
      return;
    }
    manualQuestions.push({
      question,
      options,
      correct: correct.value,
    });
    if (qText) qText.value = "";
    if (qA) qA.value = "";
    if (qB) qB.value = "";
    if (qC) qC.value = "";
    if (qD) qD.value = "";
    correct.checked = false;
    renderManualQuiz();
  });
}

if (editSummaryBtn) {
  editSummaryBtn.addEventListener("click", () => {
    summaryEditable = !summaryEditable;
    editSummaryBtn.textContent = summaryEditable
      ? "💾 Сохранить"
      : "✏️ Редактировать конспект";
    const summaryEl = summaryContainer.querySelector(".summary-editable");
    if (summaryEl) {
      summaryEl.setAttribute("contenteditable", summaryEditable ? "true" : "false");
    }
  });
}

tabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    const tabId = btn.getAttribute("data-tab");
    tabBtns.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    panelTranscript.classList.remove("active-pane");
    panelSummary.classList.remove("active-pane");
    panelQuiz.classList.remove("active-pane");
    if (panelAnalytics) {
      panelAnalytics.classList.remove("active-pane");
    }

    if (tabId === "transcript") {
      panelTranscript.classList.add("active-pane");
    } else if (tabId === "summary") {
      panelSummary.classList.add("active-pane");
    } else if (tabId === "quiz") {
      panelQuiz.classList.add("active-pane");
    } else if (tabId === "analytics" && panelAnalytics) {
      panelAnalytics.classList.add("active-pane");
    }
    if (lastTask) updateActiveTabContent(lastTask);
  });
});
