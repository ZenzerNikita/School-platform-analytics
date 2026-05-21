const summaryContainer = document.getElementById("summaryContainer");
const updatedAt = document.getElementById("updatedAt");
const POLL_INTERVAL_MS = 10000;

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>]/g, (m) => (m === "&" ? "&amp;" : m === "<" ? "&lt;" : "&gt;"));
}

function formatMarkdownToHtml(text) {
  let html = escapeHtml(text || "");
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/^\* (.*?)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*?<\/li>\n?)+/gm, "<ul>$&</ul>");
  html = html.replace(/\n\n/g, "</p><p>");
  html = `<p>${html}</p>`;
  html = html.replace(/<p><\/p>/g, "").replace(/<\/ul><ul>/g, "");
  return html;
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
  } catch (_e) {
    return;
  }
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderSummary(summary) {
  if (!summary || summary.length === 0) {
    summaryContainer.innerHTML = '<div class="status-message">Учитель еще не отправил конспект.</div>';
    return;
  }

  const tocHtml = summary
    .map((item, index) => {
      const title = item.startsWith("## ") ? item.slice(3) : `Раздел ${index + 1}`;
      return `<li class="toc-item"><a href="#section-${index}">${escapeHtml(title)}</a></li>`;
    })
    .join("");

  const sectionsHtml = summary
    .map((item, index) => {
      const text = item.startsWith("## ") ? item.slice(3) : item;
      return `
        <section id="section-${index}" class="summary-section">
          <h3>${escapeHtml(`Раздел ${index + 1}`)}</h3>
          <div class="content">${formatMarkdownToHtml(text)}</div>
        </section>
      `;
    })
    .join("");

  summaryContainer.innerHTML = `
    <div class="summary-layout">
      <aside class="summary-toc">
        <h4>Оглавление</h4>
        <ul class="toc-list">${tocHtml}</ul>
      </aside>
      <div class="summary-content">${sectionsHtml}</div>
    </div>
  `;
  renderMathInContainer(summaryContainer);
}

async function loadStudentContent() {
  try {
    const response = await fetch("/api/student/content", { cache: "no-store" });
    const data = await response.json();
    renderSummary(data.summary || []);
    updatedAt.textContent = data.updated_at ? `Обновлено: ${formatDateTime(data.updated_at)}` : "";
  } catch (_e) {
    summaryContainer.innerHTML = '<div class="status-message">Не удалось загрузить конспект.</div>';
  }
}

loadStudentContent();
setInterval(loadStudentContent, POLL_INTERVAL_MS);
