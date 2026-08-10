// Base URL for the FastAPI backend. Everything from here on talks to this.
const API_BASE = "http://localhost:8000";

// Matches the 4 languages the glossary and translator actually support
// (ai/translator.py's SOURCE_COLUMN/SYNONYM_COLUMN maps), not an
// arbitrary UI list.
const SUPPORTED_LANGUAGES = ["German", "English", "Dutch", "French"];
const LANGUAGE_LABELS_DE = {
  German: "Deutsch",
  English: "Englisch",
  Dutch: "Niederländisch",
  French: "Französisch",
};

const app = document.getElementById("app");
const stepEls = {
  upload: document.querySelector('[data-step="upload"]'),
  translate: document.querySelector('[data-step="translate"]'),
  review: document.querySelector('[data-step="review"]'),
  export: document.querySelector('[data-step="export"]'),
};

// Matches the 5 term categories the translator's tool schema can return
// (ai/translator.py's TRANSLATE_WITH_GLOSSARY_TOOL enum), not invented here.
const CATEGORY_LABELS_DE = {
  glossary: "Glossarbegriff",
  protected: "Geschützter Name",
  kitchen_technical: "Küchen-Fachbegriff",
  ambiguous: "Mehrdeutiger Begriff",
  unknown: "Unbekannt",
};

const STATUS_LABELS_DE = {
  pending: "Ausstehend",
  running: "Läuft …",
  done: "Fertig",
  failed: "Fehlgeschlagen",
  approved: "Freigegeben",
};

// Matches the 5 warning types run_quality_check() can return
// (ai/quality_check.py). Each one is backed by a real bug found in this
// project's own test runs, not a hypothetical checklist.
const WARNING_LABELS_DE = {
  contradictory_translation: "Widersprüchliche Übersetzung",
  formatting_problem: "Formatierungsproblem",
  untranslated_text: "Nicht übersetzter Text",
  missing_glossary_term: "Fehlender Glossarbegriff",
  unknown_term: "Unbekannter Begriff",
};

// Mirrors parsers/document_exporter.py's EXPORT_COMPATIBILITY. A Word/PDF
// source has no row/column structure to place in a spreadsheet, so xlsx is
// only offered when the source itself was xlsx.
const EXPORT_COMPATIBILITY = {
  xlsx: ["docx", "xlsx", "pdf"],
  docx: ["docx", "pdf"],
  pdf: ["docx", "pdf"],
};
const FORMAT_LABELS_DE = {
  docx: "Word (.docx)",
  xlsx: "Excel (.xlsx)",
  pdf: "PDF (.pdf)",
};
const EXPORT_MODE_LABELS_DE = {
  translation_only: "Nur Übersetzung",
  bilingual: "Original + Übersetzung",
  quality_report: "Prüfbericht",
};

// Holds the uploaded project once step 1 succeeds, the queued translation
// jobs once step 3 succeeds, and each job's full result (with segments)
// once polling picks up a "done" status. Later steps all key off these.
const state = {
  project: null,
  translations: null,
  translationDetails: {},
  qualityChecks: {},
  currentTranslationId: null,
};

let pollIntervalId = null;

// The sidebar's own copy of the project list, separate from `state`. It
// persists across every screen (unlike #app's contents), so it's kept as
// module-level data rather than something renderX() functions manage.
let sidebarProjects = [];

async function loadSidebarProjects() {
  try {
    sidebarProjects = await apiFetch(`${API_BASE}/projects`, {}, "Fehler beim Laden der Projekte");
    renderSidebar();
  } catch (err) {
    // A sidebar that fails to load must never block the rest of the app --
    // it's a convenience layer on top of data that's already saved, not a
    // dependency the main wizard needs to function.
    console.error("Failed to load project list:", err);
  }
}

function renderSidebar() {
  const listEl = document.getElementById("sidebar-project-list");
  if (!listEl) return;

  if (sidebarProjects.length === 0) {
    listEl.innerHTML = `<p class="sidebar-empty">Noch keine Projekte</p>`;
    return;
  }

  listEl.innerHTML = sidebarProjects
    .map((p) => {
      const isActive = state.project?.id === p.id;
      const langLabel = LANGUAGE_LABELS_DE[p.source_language] ?? p.source_language ?? "?";
      const dateLabel = new Date(p.uploaded_at).toLocaleDateString("de-DE");
      return `
        <div class="sidebar-project-item ${isActive ? "is-active" : ""}" data-project-id="${p.id}">
          <span class="sidebar-project-filename">${escapeHtml(p.filename)}</span>
          <span class="sidebar-project-meta"><span>${langLabel}</span><span>&middot;</span><span>${dateLabel}</span></span>
        </div>
      `;
    })
    .join("");

  listEl.querySelectorAll(".sidebar-project-item").forEach((el) =>
    el.addEventListener("click", () => openProject(el.dataset.projectId))
  );
}

// Reopening a project (spec §1: "Projekt erneut öffnen") has to land on
// different screens depending on how far it got: straight back to language
// selection if no translation was ever started, or the status/result view
// if one was. Reuses renderTranslateScreen()/renderTranslationQueued()
// unchanged -- this is a routing decision, not a new screen.
async function openProject(projectId) {
  const project = sidebarProjects.find((p) => p.id === projectId);
  if (!project) return;

  stopPolling();
  state.project = project;
  state.translations = null;
  state.translationDetails = {};
  state.qualityChecks = {};
  state.currentTranslationId = null;
  termRegistry = [];
  renderSidebar();

  render(`<h1>${escapeHtml(project.filename)}</h1><p class="subtitle">Wird geladen …</p>`);

  try {
    const translations = await apiFetch(
      `${API_BASE}/projects/${projectId}/translations`,
      {},
      "Fehler beim Laden der Übersetzungen"
    );

    Object.values(stepEls).forEach((el) => el.classList.remove("is-active", "is-done"));
    markStepDone("upload");

    if (translations.length === 0) {
      renderTranslateScreen();
    } else {
      renderTranslationQueued(translations);
    }
  } catch (err) {
    render(`<h1>Fehler</h1><p class="status-message status-error">${escapeHtml(err.message)}</p>`);
  }
}

function stopPolling() {
  if (pollIntervalId) {
    clearInterval(pollIntervalId);
    pollIntervalId = null;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Shared by every mutating call site: throws the backend's own `detail`
// message (or a fallback) on any non-2xx response, otherwise returns the
// parsed JSON body. This used to be the same 4-line check/parse/throw block
// copy-pasted 4 times, and was missing entirely on 2 read-only calls that
// had no error handling at all.
async function apiFetch(url, options, fallbackMessage) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `${fallbackMessage} (Status ${response.status})`);
  }
  return response.json();
}

// The 3 segment-edit handlers all need to look up the currently-displayed
// translation's segment list by id. One shared lookup instead of
// re-writing `state.translationDetails[state.currentTranslationId].segments.find(...)`
// at each call site.
function getCurrentSegment(segmentId) {
  return state.translationDetails[state.currentTranslationId].segments.find((s) => s.id === segmentId);
}

// Holds full term objects for the currently-rendered result view, indexed
// by the number embedded in each highlighted span's data-term-idx. Reset
// once per renderResultView() call. Spec section 5 requires click-to-reveal
// (used translation, alternatives, description, glossary entry), which a
// native `title` tooltip can't show as structured fields, so spans carry an
// index into this registry instead of the term data itself.
let termRegistry = [];

// Wraps every occurrence of each term's translated form in a highlighted,
// clickable span. Two-pass (mark first, then style) so a later term's regex
// can never accidentally match inside an already-inserted <span>.
function highlightTerms(text, terms) {
  const escaped = escapeHtml(text);
  const usable = (terms ?? []).filter((t) => t.used_translation && t.used_translation.trim());
  if (usable.length === 0) return escaped;

  // Longest translated term first, so e.g. "Griffleiste" is matched whole
  // before the shorter "Griff" can carve a piece out of it.
  const sorted = [...usable].sort((a, b) => b.used_translation.length - a.used_translation.length);

  const replacements = [];
  let html = escaped;

  sorted.forEach((term) => {
    const needle = escapeHtml(term.used_translation);
    if (!needle) return;
    const re = new RegExp(escapeRegex(needle), "gi");
    html = html.replace(re, (match) => {
      const token = `@@T${replacements.length}@@`;
      replacements.push({ token, match, term });
      return token;
    });
  });

  replacements.forEach(({ token, match, term }) => {
    const idx = termRegistry.length;
    termRegistry.push(term);
    const span = `<span class="term term-${term.category}" data-term-idx="${idx}">${match}</span>`;
    html = html.replace(token, span);
  });

  return html;
}

function renderTermDetailPanel(term) {
  const panel = document.getElementById("term-detail-panel");
  if (!term) {
    panel.classList.add("is-hidden");
    panel.innerHTML = "";
    return;
  }

  const label = CATEGORY_LABELS_DE[term.category] ?? term.category;
  const alternatives = term.alternative_translations ?? [];
  const entry = term.glossary_entry;

  let entryHtml = "";
  if (entry) {
    const rows = [
      ["Deutsch", entry["Source Term (DE)"]],
      ["Englisch", entry["English"]],
      ["Niederländisch", entry["Dutch"]],
      ["Französisch", entry["French"]],
    ].filter(([, value]) => value);
    entryHtml = `
      <div class="term-detail-field">
        <span class="term-detail-label">Glossareintrag</span>
        <div class="term-detail-glossary-entry">
          ${rows.map(([lang, value]) => `<div><span>${lang}</span><span>${escapeHtml(value)}</span></div>`).join("")}
        </div>
      </div>
    `;
  }

  panel.innerHTML = `
    <div class="term-detail-header">
      <span class="term-badge term-${term.category}">${label}</span>
      <button id="term-detail-close" class="term-detail-close-btn" aria-label="Schließen">×</button>
    </div>
    <div class="term-detail-field">
      <span class="term-detail-label">Verwendete Übersetzung</span>
      <span>${escapeHtml(term.used_translation)}</span>
    </div>
    ${
      alternatives.length > 0
        ? `<div class="term-detail-field">
             <span class="term-detail-label">Alternative Übersetzungen</span>
             <span>${alternatives.map(escapeHtml).join(", ")}</span>
           </div>`
        : ""
    }
    <div class="term-detail-field">
      <span class="term-detail-label">Beschreibung</span>
      <span>${escapeHtml(term.description ?? "")}</span>
    </div>
    ${entryHtml}
  `;
  panel.classList.remove("is-hidden");

  document.getElementById("term-detail-close").addEventListener("click", () => renderTermDetailPanel(null));
}

function render(html) {
  app.innerHTML = html;
}

function setActiveStep(stepName) {
  Object.values(stepEls).forEach((el) => el.classList.remove("is-active"));
  stepEls[stepName].classList.add("is-active");
}

function markStepDone(stepName) {
  stepEls[stepName].classList.add("is-done");
}

function renderUploadScreen() {
  setActiveStep("upload");
  render(`
    <h1>Dokument hochladen</h1>
    <p class="subtitle">Unterstützte Formate: Word (.docx), Excel (.xlsx), PDF (.pdf)</p>

    <div class="dropzone" id="dropzone">
      <p class="dropzone-text">Datei hierher ziehen oder klicken zum Auswählen</p>
      <input type="file" id="file-input" accept=".docx,.xlsx,.pdf" hidden>
    </div>

    <div id="upload-status"></div>
  `);

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");

  dropzone.addEventListener("click", () => fileInput.click());

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("is-dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
    if (e.dataTransfer.files.length > 0) {
      uploadFile(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length > 0) {
      uploadFile(fileInput.files[0]);
    }
  });
}

async function uploadFile(file) {
  const statusEl = document.getElementById("upload-status");
  statusEl.innerHTML = `<p class="status-message">Wird hochgeladen und analysiert …</p>`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const project = await apiFetch(`${API_BASE}/projects`, { method: "POST", body: formData }, "Fehler beim Hochladen");
    state.project = project;
    renderUploadResult(project);
    loadSidebarProjects(); // new project should appear in the sidebar immediately
  } catch (err) {
    statusEl.innerHTML = `<p class="status-message status-error">Fehler: ${err.message}</p>`;
  }
}

function renderUploadResult(project) {
  markStepDone("upload");
  setActiveStep("translate");

  const confidencePct = Math.round((project.detection_confidence ?? 0) * 100);
  const languageLabel = LANGUAGE_LABELS_DE[project.source_language] ?? project.source_language ?? "unbekannt";

  render(`
    <h1>Dokument hochgeladen</h1>
    <div class="result-card">
      <div class="result-row"><span>Dateiname</span><strong>${project.filename}</strong></div>
      <div class="result-row"><span>Format</span><strong>${project.source_format.toUpperCase()}</strong></div>
      <div class="result-row"><span>Erkannte Sprache</span><strong>${languageLabel}</strong></div>
      <div class="result-row"><span>Erkennungssicherheit</span><strong>${confidencePct}%</strong></div>
      <div class="result-row"><span>Segmente</span><strong>${project.segment_count}</strong></div>
    </div>
    <button id="to-translate-btn">Weiter zur Sprachauswahl</button>
  `);

  document.getElementById("to-translate-btn").addEventListener("click", renderTranslateScreen);
}

function renderTranslateScreen() {
  setActiveStep("translate");

  const project = state.project;
  const sourceLabel = LANGUAGE_LABELS_DE[project.source_language] ?? project.source_language;
  const targetOptions = SUPPORTED_LANGUAGES.filter((lang) => lang !== project.source_language);

  render(`
    <h1>Zielsprachen auswählen</h1>
    <p class="subtitle">Quellsprache: ${sourceLabel}</p>

    <div class="language-options">
      ${targetOptions
        .map(
          (lang) => `
        <label class="language-option">
          <input type="checkbox" name="target-language" value="${lang}">
          <span>${LANGUAGE_LABELS_DE[lang]}</span>
        </label>
      `
        )
        .join("")}
    </div>

    <label class="glossary-toggle">
      <input type="checkbox" id="use-glossary" checked>
      <span>Firmenglossar verwenden (empfohlen)</span>
    </label>

    <button id="start-translation-btn" disabled>Übersetzung starten</button>

    <div id="translate-status"></div>
  `);

  const checkboxes = document.querySelectorAll('input[name="target-language"]');
  const startBtn = document.getElementById("start-translation-btn");

  checkboxes.forEach((cb) =>
    cb.addEventListener("change", () => {
      startBtn.disabled = !Array.from(checkboxes).some((c) => c.checked);
    })
  );

  startBtn.addEventListener("click", startTranslation);
}

async function startTranslation() {
  const checkboxes = document.querySelectorAll('input[name="target-language"]:checked');
  const targetLanguages = Array.from(checkboxes).map((cb) => cb.value);
  const useGlossary = document.getElementById("use-glossary").checked;
  const startBtn = document.getElementById("start-translation-btn");
  const statusEl = document.getElementById("translate-status");

  startBtn.disabled = true;
  statusEl.innerHTML = `<p class="status-message">Übersetzung wird gestartet …</p>`;

  try {
    const translations = await apiFetch(
      `${API_BASE}/projects/${state.project.id}/translations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_languages: targetLanguages, use_glossary: useGlossary }),
      },
      "Fehler beim Starten"
    );
    state.translations = translations;
    renderTranslationQueued(translations);
  } catch (err) {
    statusEl.innerHTML = `<p class="status-message status-error">Fehler: ${err.message}</p>`;
    startBtn.disabled = false;
  }
}

function renderTranslationQueued(translations) {
  markStepDone("translate");
  setActiveStep("review");
  state.translations = translations;

  render(`
    <h1>Übersetzung läuft</h1>
    <p class="subtitle">Der Status wird automatisch aktualisiert.</p>
    <div class="result-card" id="translation-status-list"></div>
  `);

  renderTranslationStatusList();

  stopPolling();
  pollTranslations();
  pollIntervalId = setInterval(pollTranslations, 2000);
}

function renderTranslationStatusList() {
  const listEl = document.getElementById("translation-status-list");
  if (!listEl) return; // user has since navigated to a result view

  listEl.innerHTML = state.translations
    .map((t) => {
      const langLabel = LANGUAGE_LABELS_DE[t.target_language] ?? t.target_language;
      const statusLabel = STATUS_LABELS_DE[t.status] ?? t.status;

      let action = "";
      if (t.status === "done" || t.status === "approved") {
        action = `<button class="view-result-btn" data-id="${t.id}">Ergebnis anzeigen</button>`;
      } else if (t.status === "failed") {
        action = `<span class="status-error">${escapeHtml(t.error ?? "Unbekannter Fehler")}</span>`;
      }

      return `
        <div class="result-row">
          <span>${langLabel}</span>
          <div class="status-cell">
            <span class="status-badge status-${t.status}">${statusLabel}</span>
            ${action}
          </div>
        </div>
      `;
    })
    .join("");

  listEl.querySelectorAll(".view-result-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      stopPolling();
      renderResultView(btn.dataset.id);
    })
  );
}

async function pollTranslations() {
  const stillWaiting = state.translations.filter((t) => t.status === "pending" || t.status === "running");
  if (stillWaiting.length === 0) {
    stopPolling();
    return;
  }

  await Promise.all(
    stillWaiting.map(async (t) => {
      const resp = await fetch(`${API_BASE}/projects/${state.project.id}/translations/${t.id}`);
      if (!resp.ok) return;
      const updated = await resp.json();
      const idx = state.translations.findIndex((x) => x.id === t.id);
      state.translations[idx] = updated;
      if (updated.status === "done") {
        state.translationDetails[t.id] = updated; // detail response already carries segments
      }
    })
  );

  renderTranslationStatusList();
}

async function renderResultView(translationId) {
  setActiveStep("review");
  render(`<h1>Ergebnis</h1><p class="subtitle">Wird geladen …</p>`);

  let detail = state.translationDetails[translationId];
  if (!detail) {
    detail = await apiFetch(`${API_BASE}/projects/${state.project.id}/translations/${translationId}`, {}, "Fehler beim Laden");
    state.translationDetails[translationId] = detail;
  }

  let qualityCheck = state.qualityChecks[translationId];
  if (!qualityCheck) {
    qualityCheck = await apiFetch(
      `${API_BASE}/projects/${state.project.id}/translations/${translationId}/quality-check`,
      {},
      "Fehler beim Laden der Qualitätsprüfung"
    );
    state.qualityChecks[translationId] = qualityCheck;
  }

  const langLabel = LANGUAGE_LABELS_DE[detail.target_language] ?? detail.target_language;
  markStepDone("review");
  setActiveStep("export");

  termRegistry = [];
  state.currentTranslationId = translationId;
  const editable = detail.status === "done";

  render(`
    <div class="result-header">
      <h1>Ergebnis: ${langLabel}</h1>
      <button id="back-to-status-btn" class="secondary-btn">Zurück zur Übersicht</button>
    </div>

    ${renderQualitySection(qualityCheck)}

    <div class="term-legend">
      ${Object.entries(CATEGORY_LABELS_DE)
        .map(([cat, label]) => `<span class="legend-item"><span class="legend-dot term-${cat}"></span>${label}</span>`)
        .join("")}
    </div>

    ${editable ? `<p class="subtitle">Klicken Sie auf ✎, um eine Übersetzung zu korrigieren.</p>` : ""}

    <div class="segment-list">
      ${detail.segments.map((s) => renderSegmentRow(s, editable)).join("")}
    </div>

    <div id="term-detail-panel" class="term-detail-panel is-hidden"></div>

    ${renderApprovalSection(detail)}
  `);

  document.getElementById("back-to-status-btn").addEventListener("click", () => renderTranslationQueued(state.translations));

  document.querySelector(".segment-list").addEventListener("click", (e) => {
    const termSpan = e.target.closest(".term");
    if (termSpan) {
      renderTermDetailPanel(termRegistry[termSpan.dataset.termIdx]);
      return;
    }
    const editBtn = e.target.closest(".edit-segment-btn");
    if (editBtn) {
      startEditingSegment(editBtn.dataset.segmentId);
      return;
    }
    const saveBtn = e.target.closest(".save-segment-btn");
    if (saveBtn) {
      saveEditedSegment(saveBtn.dataset.segmentId);
      return;
    }
    const cancelBtn = e.target.closest(".cancel-segment-btn");
    if (cancelBtn) {
      cancelEditingSegment(cancelBtn.dataset.segmentId);
    }
  });

  if (detail.status === "approved") {
    wireExportSection(translationId);
    document.getElementById("new-project-btn")?.addEventListener("click", resetApp);
  } else {
    document.getElementById("approve-btn")?.addEventListener("click", () => approveTranslation(translationId));
  }
}

function resetApp() {
  stopPolling();
  state.project = null;
  state.translations = null;
  state.translationDetails = {};
  state.qualityChecks = {};
  state.currentTranslationId = null;
  termRegistry = [];

  Object.values(stepEls).forEach((el) => {
    el.classList.remove("is-active", "is-done");
  });

  renderUploadScreen();
  renderSidebar(); // clears the now-stale active highlight
}

function renderApprovalSection(detail) {
  if (detail.status === "approved") {
    return `
      <div class="approval-section approval-done">
        <span class="approval-icon">✓</span>
        <span>Freigegeben: Export ist verfügbar.</span>
      </div>
      ${renderExportSection()}
      <div class="new-translation-section">
        <button id="new-project-btn" class="secondary-btn">Neue Übersetzung starten</button>
      </div>
    `;
  }

  return `
    <div class="approval-section approval-pending">
      <p class="subtitle">Vor dem Export muss die Übersetzung freigegeben werden (spec section 9).</p>
      <button id="approve-btn">Übersetzung freigeben</button>
      <div id="approve-status"></div>
    </div>
  `;
}

async function approveTranslation(translationId) {
  const btn = document.getElementById("approve-btn");
  const statusEl = document.getElementById("approve-status");
  btn.disabled = true;
  statusEl.innerHTML = `<p class="status-message">Wird freigegeben …</p>`;

  try {
    const updated = await apiFetch(
      `${API_BASE}/projects/${state.project.id}/translations/${translationId}/approve`,
      { method: "POST" },
      "Fehler bei der Freigabe"
    );

    const detail = state.translationDetails[translationId];
    detail.status = updated.status;
    const idx = state.translations?.findIndex((t) => t.id === translationId) ?? -1;
    if (idx !== -1) state.translations[idx].status = updated.status;

    renderResultView(translationId);
  } catch (err) {
    statusEl.innerHTML = `<p class="status-message status-error">Fehler: ${err.message}</p>`;
    btn.disabled = false;
  }
}

function renderSegmentRow(segment, editable) {
  const translationHtml = segment.error
    ? `<span class="status-error">Fehler: ${escapeHtml(segment.error)}</span>`
    : highlightTerms(segment.translation_text ?? "", segment.terms);

  const suspiciousBadge = segment.suspicious ? `<span class="suspicious-badge">Zu prüfen</span>` : "";
  const editedBadge = segment.edited ? `<span class="edited-badge">Bearbeitet</span>` : "";
  // Spec section 6: editing is only offered between "done" and "approved" --
  // nothing to correct before translation finishes, and an approved
  // translation is exported as-is (enforced server-side too, not just hidden here).
  const editBtn = editable && !segment.error
    ? `<button class="edit-segment-btn" data-segment-id="${segment.id}" title="Übersetzung bearbeiten">✎</button>`
    : "";

  return `
    <div class="segment-row ${segment.suspicious ? "is-suspicious" : ""}" data-segment-row-id="${segment.id}">
      <div class="segment-source">${escapeHtml(segment.source_text)}</div>
      <div class="segment-translation">
        <span class="segment-translation-text">${translationHtml}</span>
        ${suspiciousBadge}${editedBadge}${editBtn}
      </div>
    </div>
  `;
}

function startEditingSegment(segmentId) {
  const segment = getCurrentSegment(segmentId);
  const row = document.querySelector(`[data-segment-row-id="${segmentId}"] .segment-translation`);

  row.innerHTML = `
    <textarea class="segment-edit-textarea">${escapeHtml(segment.translation_text ?? "")}</textarea>
    <div class="segment-edit-actions">
      <button class="save-segment-btn" data-segment-id="${segmentId}">Speichern</button>
      <button class="cancel-segment-btn secondary-btn" data-segment-id="${segmentId}">Abbrechen</button>
    </div>
  `;
  row.querySelector("textarea").focus();
}

function cancelEditingSegment(segmentId) {
  const detail = state.translationDetails[state.currentTranslationId];
  const segment = getCurrentSegment(segmentId);
  const rowEl = document.querySelector(`[data-segment-row-id="${segmentId}"]`);
  rowEl.outerHTML = renderSegmentRow(segment, detail.status === "done");
}

async function saveEditedSegment(segmentId) {
  const translationId = state.currentTranslationId;
  const row = document.querySelector(`[data-segment-row-id="${segmentId}"]`);
  const textarea = row.querySelector("textarea");
  const newText = textarea.value;
  const saveBtn = row.querySelector(".save-segment-btn");
  saveBtn.disabled = true;

  try {
    const updated = await apiFetch(
      `${API_BASE}/projects/${state.project.id}/translations/${translationId}/segments/${segmentId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ translation_text: newText }),
      },
      "Fehler beim Speichern"
    );

    const detail = state.translationDetails[translationId];
    const idx = detail.segments.findIndex((s) => s.id === segmentId);
    detail.segments[idx] = updated;

    row.outerHTML = renderSegmentRow(updated, detail.status === "done");
  } catch (err) {
    row.querySelector(".segment-edit-actions").insertAdjacentHTML(
      "afterend",
      `<p class="status-message status-error">Fehler: ${escapeHtml(err.message)}</p>`
    );
    saveBtn.disabled = false;
  }
}

function renderQualitySection(qualityCheck) {
  if (qualityCheck.warning_count === 0) {
    return `
      <div class="quality-section quality-clean">
        <span class="quality-icon">✓</span>
        <span>Qualitätsprüfung: keine Auffälligkeiten gefunden.</span>
      </div>
    `;
  }

  return `
    <div class="quality-section quality-has-warnings">
      <h2>Qualitätsprüfung: ${qualityCheck.warning_count} Hinweis(e)</h2>
      <div class="warning-list">
        ${qualityCheck.warnings
          .map(
            (w) => `
          <div class="warning-row">
            <span class="warning-badge warning-${w.type}">${WARNING_LABELS_DE[w.type] ?? w.type}</span>
            <span class="warning-message">${escapeHtml(w.message)}</span>
          </div>
        `
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderExportSection() {
  const allowedFormats = EXPORT_COMPATIBILITY[state.project.source_format] ?? [];

  return `
    <div class="export-section">
      <h2>Export</h2>
      <a class="download-original-link" href="${API_BASE}/projects/${state.project.id}/file" download>Original herunterladen</a>
      <div class="export-controls">
        <label class="export-field">
          Format
          <select id="export-format">
            ${allowedFormats.map((f) => `<option value="${f}">${FORMAT_LABELS_DE[f]}</option>`).join("")}
          </select>
        </label>
        <label class="export-field">
          Inhalt
          <select id="export-mode">
            ${Object.entries(EXPORT_MODE_LABELS_DE)
              .map(([mode, label]) => `<option value="${mode}">${label}</option>`)
              .join("")}
          </select>
        </label>
        <a id="export-download-link" class="download-btn" href="#" download>Herunterladen</a>
      </div>
    </div>
  `;
}

function wireExportSection(translationId) {
  const formatSelect = document.getElementById("export-format");
  const modeSelect = document.getElementById("export-mode");
  const link = document.getElementById("export-download-link");
  if (!formatSelect || !link) return; // no format compatible with this source at all

  function updateHref() {
    const url = new URL(`${API_BASE}/projects/${state.project.id}/translations/${translationId}/export`);
    url.searchParams.set("format", formatSelect.value);
    url.searchParams.set("mode", modeSelect.value);
    link.href = url.toString();
  }

  formatSelect.addEventListener("change", updateHref);
  modeSelect.addEventListener("change", updateHref);
  updateHref();
}

function init() {
  renderUploadScreen();
  loadSidebarProjects();
  document.getElementById("sidebar-new-project-btn").addEventListener("click", resetApp);
}

init();
