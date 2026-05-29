const summaryContainer = document.getElementById("summaryContainer");
const quizContainer = document.getElementById("quizContainer");
const tabBtns = document.querySelectorAll(".tab-btn");
const panels = {
  summary: document.getElementById("panelSummary"),
  quiz: document.getElementById("panelQuiz"),
};
let currentTest = null;

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

function statusMessage(message) {
  return `<div class="status-message">${escapeHtml(message)}</div>`;
}

function getTaskId() {
  const url = new URL(window.location.href);
  const pathParts = url.pathname.split("/").filter(Boolean);
  if (pathParts[0] === "material" && pathParts[1]) return decodeURIComponent(pathParts[1]);
  return url.searchParams.get("task_id") || "";
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

function renderSummary(summary) {
  if (!Array.isArray(summary) || !summary.length) {
    summaryContainer.innerHTML = statusMessage("Учитель еще не отправил конспект.");
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

function renderQuizResults(score, total) {
  const percent = total > 0 ? Math.round((score / total) * 100) : 0;
  const level = percent >= 80 ? "good" : percent >= 50 ? "medium" : "low";
  const recommendation = percent >= 80
    ? "Отличный результат. Можно переходить к следующей теме."
    : percent >= 50
      ? "База усвоена частично. Стоит еще раз пройтись по конспекту и ошибочным вопросам."
      : "Есть заметные пробелы. Лучше перечитать конспект и повторить решение шаг за шагом.";

  return `
    <div class="quiz-results-card" style="margin-top: 1rem;">
      <h3>Результат</h3>
      <div class="quiz-results-grid">
        <div class="quiz-result-row">
          <div class="quiz-result-subtopic">Правильные ответы</div>
          <div class="quiz-result-progress-line">
            <div class="quiz-result-progress-fill ${level}" style="width: ${percent}%"></div>
          </div>
          <div class="quiz-result-percent">${score}/${total}</div>
        </div>
      </div>
      <div class="quiz-result-recommendation">${escapeHtml(recommendation)}</div>
    </div>
  `;
}

function attachQuizHandlers(test) {
  const submitBtn = document.getElementById("quizSubmitBtn");
  const resultsNode = document.getElementById("quizResults");
  if (!submitBtn || !resultsNode) return;

  submitBtn.addEventListener("click", () => {
    const questions = Array.isArray(test?.questions) ? test.questions : [];
    let score = 0;

    questions.forEach((question, questionIndex) => {
      const selected = document.querySelector(`input[name="question-${questionIndex}"]:checked`);
      const selectedIndex = selected ? Number(selected.value) : -1;
      const correctIndex = Number(question.correct_answer);
      if (selectedIndex === correctIndex) {
        score += 1;
      }

      document.querySelectorAll(`[data-question-index="${questionIndex}"]`).forEach((node) => {
        const optionIndex = Number(node.getAttribute("data-option-index"));
        node.classList.remove("correct-highlight", "wrong-highlight");
        if (optionIndex === correctIndex) {
          node.classList.add("correct-highlight");
        } else if (optionIndex === selectedIndex) {
          node.classList.add("wrong-highlight");
        }
      });
    });

    resultsNode.innerHTML = renderQuizResults(score, questions.length);
  });
}

function renderStructuredQuiz(test) {
  const questions = Array.isArray(test?.questions) ? test.questions : [];
  if (!questions.length) return false;

  const questionsHtml = questions.map((question, questionIndex) => {
    const options = Array.isArray(question.options) ? question.options : [];
    const optionsHtml = options.map((option, optionIndex) => `
      <div class="quiz-option" data-question-index="${questionIndex}" data-option-index="${optionIndex}">
        <input type="radio" name="question-${questionIndex}" value="${optionIndex}" id="question-${questionIndex}-option-${optionIndex}">
        <label for="question-${questionIndex}-option-${optionIndex}">${formatMarkdownToHtml(String(option || ""))}</label>
      </div>
    `).join("");

    return `
      <div class="quiz-item">
        <div class="quiz-question">${formatMarkdownToHtml(question.question || "")}</div>
        ${optionsHtml}
      </div>
    `;
  }).join("");

  quizContainer.innerHTML = `
    <div class="quiz-results-card">
      <h3>${escapeHtml(test.title || "Тест")}</h3>
      <div class="quiz-list">${questionsHtml}</div>
      <button id="quizSubmitBtn" class="btn-secondary" type="button">Проверить ответы</button>
      <div id="quizResults"></div>
    </div>
  `;

  setTimeout(() => {
    renderMathInContainer(quizContainer);
    highlightCodeInContainer(quizContainer);
  }, 30);
  attachQuizHandlers(test);
  return true;
}

function renderQuiz(test, quizText) {
  if (renderStructuredQuiz(test)) {
    return;
  }

  if (!quizText) {
    quizContainer.innerHTML = statusMessage("Тест пока не опубликован.");
    return;
  }
  quizContainer.innerHTML = `
    <div class="quiz-results-card">
      <h3>Тест</h3>
      <div class="quiz-markdown">${formatMarkdownToHtml(quizText)}</div>
    </div>
  `;
  setTimeout(() => {
    renderMathInContainer(quizContainer);
    highlightCodeInContainer(quizContainer);
  }, 30);
}

async function loadStudentContent() {
  const taskId = getTaskId();
  try {
    const endpoint = taskId
      ? `/api/student/${encodeURIComponent(taskId)}`
      : "/api/student/content";
    const response = await fetch(endpoint, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Не удалось загрузить материалы.");
    }
    if (!data.task_id || (!Array.isArray(data.summary) || !data.summary.length) && !data.test && !data.quiz_text) {
      summaryContainer.innerHTML = statusMessage("Учитель еще не опубликовал материалы.");
      quizContainer.innerHTML = statusMessage("Тест пока не опубликован.");
      return;
    }
    currentTest = data.test || null;
    renderSummary(data.summary || []);
    renderQuiz(currentTest, data.quiz_text || "");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не удалось загрузить материалы.";
    summaryContainer.innerHTML = statusMessage(message);
    quizContainer.innerHTML = statusMessage(message);
  }
}

tabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    const tabId = btn.getAttribute("data-tab");
    tabBtns.forEach((item) => item.classList.remove("active"));
    btn.classList.add("active");
    Object.values(panels).forEach((panel) => panel.classList.remove("active-pane"));
    panels[tabId]?.classList.add("active-pane");
  });
});

loadStudentContent();
