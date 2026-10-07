/**
 * popup.js — GCC Job Radar & Resume Tailor (Redesigned)
 * Connects browser tab data to local Bridge daemon on http://127.0.0.1:8765
 */

const BRIDGE_URL = "http://127.0.0.1:8765";

// Header DOM Elements
const bridgeStatus = document.getElementById("bridge-status");
const bridgeWarning = document.getElementById("bridge-warning");
const btnRetryBridge = document.getElementById("btn-retry-bridge");
const btnMenuOverflow = document.getElementById("btn-menu-overflow");
const overflowMenu = document.getElementById("overflow-menu");
const btnViewMasterPdf = document.getElementById("btn-view-master-pdf");
const btnReextract = document.getElementById("btn-reextract");

// Job Card Elements
const summaryCompany = document.getElementById("summary-company");
const summaryTitle = document.getElementById("summary-title");
const btnEditJobMeta = document.getElementById("btn-edit-job-meta");
const jobMetaInputs = document.getElementById("job-meta-inputs");
const inputCompany = document.getElementById("input-company");
const inputTitle = document.getElementById("input-title");
const inputJd = document.getElementById("input-jd");
const jdCharCount = document.getElementById("jd-char-count");
const btnToggleJd = document.getElementById("btn-toggle-jd");
const jdTextareaWrap = document.getElementById("jd-textarea-wrap");
const toggleJdText = document.getElementById("toggle-jd-text");
const toggleJdIcon = document.getElementById("toggle-jd-icon");

// Company Radar Intelligence & Direct ATS Elements (Phase 2)
const companyIntelligenceBar = document.getElementById("company-intelligence-bar");
const ciStatusBadge = document.getElementById("ci-status-badge");
const ciHistoryText = document.getElementById("ci-history-text");
const btnDirectAts = document.getElementById("btn-direct-ats");
const btnDirectAtsIcon = document.getElementById("btn-direct-ats-icon");
const btnDirectAtsLabel = document.getElementById("btn-direct-ats-label");

// Tab Switch Banner
const tabSwitchBanner = document.getElementById("tab-switch-banner");
const tabSwitchTitle = document.getElementById("tab-switch-title");
const btnTabSwitchLoad = document.getElementById("btn-tab-switch-load");

// Actions Section
const btnEvaluate = document.getElementById("btn-evaluate");
const btnTailor = document.getElementById("btn-tailor");
const btnOutreach = document.getElementById("btn-outreach");
const btnAutofill = document.getElementById("btn-autofill");
const btnDismissMain = document.getElementById("btn-dismiss-main");
const autofillBanner = document.getElementById("autofill-banner");
const dismissBanner = document.getElementById("dismiss-banner");

// Results Area & Empty State
const resultsArea = document.getElementById("results-area");
const emptyState = document.getElementById("empty-state");
const loadingCard = document.getElementById("loading-card");
const loadingText = document.getElementById("loading-text");

// Evaluation Card Elements
const evalCard = document.getElementById("eval-card");
const verdictBadge = document.getElementById("verdict-badge");
const btnDismissEval = document.getElementById("btn-dismiss-eval");
const scoreVal = document.getElementById("score-val");
const gaugeFill = document.getElementById("gauge-fill");
const oneLineReason = document.getElementById("one-line-reason");
const applyAction = document.getElementById("apply-action");
const matchedSkillsContainer = document.getElementById("matched-skills-container");
const matchedSkills = document.getElementById("matched-skills");
const missingSkillsContainer = document.getElementById("missing-skills-container");
const missingSkills = document.getElementById("missing-skills");
const greenFlagsContainer = document.getElementById("green-flags-container");
const greenFlagsList = document.getElementById("green-flags-list");
const hardBlocksContainer = document.getElementById("hard-blocks-container");
const hardBlocksList = document.getElementById("hard-blocks-list");
const warningsContainer = document.getElementById("warnings-container");
const warningsList = document.getElementById("warnings-list");

// Outreach Card Elements
const outreachCard = document.getElementById("outreach-card");
const btnSearchRecruiters = document.getElementById("btn-search-recruiters");
const tabOutreachLinkedin = document.getElementById("tab-outreach-linkedin");
const tabOutreachEmail = document.getElementById("tab-outreach-email");
const tabOutreachReferral = document.getElementById("tab-outreach-referral");
const audiencePills = document.querySelectorAll(".audience-pill");
const outreachSubjectRow = document.getElementById("outreach-subject-row");
const outreachSubjectInput = document.getElementById("outreach-subject-input");
const btnCopySubject = document.getElementById("btn-copy-subject");
const outreachBodyTextarea = document.getElementById("outreach-body-textarea");
const outreachCharCount = document.getElementById("outreach-char-count");
const btnCopyOutreach = document.getElementById("btn-copy-outreach");
const copyOutreachText = document.getElementById("copy-outreach-text");

// Screening Assistant Card Elements
const screeningCard = document.getElementById("screening-card");
const btnCloseScreening = document.getElementById("btn-close-screening");
const chipBtns = document.querySelectorAll(".chip-btn");
const screeningQuestionInput = document.getElementById("screening-question-input");
const btnGenerateAnswer = document.getElementById("btn-generate-answer");
const screeningAnswerBox = document.getElementById("screening-answer-box");
const screeningAnswerTextarea = document.getElementById("screening-answer-textarea");
const screeningSourceTag = document.getElementById("screening-source-tag");
const btnCopyScreening = document.getElementById("btn-copy-screening");
const copyScreeningText = document.getElementById("copy-screening-text");
const btnInsertScreening = document.getElementById("btn-insert-screening");

// Tailor Card Elements
const tailorCard = document.getElementById("tailor-card");
const previewStatusTitle = document.getElementById("preview-status-title");
const tailorFilename = document.getElementById("tailor-filename");
const btnOpenFullTab = document.getElementById("btn-open-full-tab");
const pdfPreviewFrame = document.getElementById("pdf-preview-frame");
const inputFeedback = document.getElementById("input-feedback");
const btnRefine = document.getElementById("btn-refine");
const refineStatus = document.getElementById("refine-status");
const btnConfirmDownload = document.getElementById("btn-confirm-download");
const btnViewDiff = document.getElementById("btn-view-diff");
const btnMarkApplied = document.getElementById("btn-mark-applied");
const diffContainer = document.getElementById("diff-container");
const diffContent = document.getElementById("diff-content");

// Footer Elements
const footerDbStats = document.getElementById("footer-db-stats");

// State Variables
let currentActiveUrl = "";
let currentEvaluation = null;
let currentFilename = "";
let currentDownloadUrl = "";
let currentViewUrl = "";
let currentOutreachData = null;
let currentOutreachFormat = "linkedin";
let currentAudience = "recruiter";
let candidateProfileCache = null;
let lastFocusedFieldId = null;

// Initialize on DOM ready
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  updateActionButtonsState();
  updateEmptyStateVisibility();
  await checkBridgeHealth();
  await loadJobFromActiveTab();
});

function setupEventListeners() {
  // Input tracking
  inputJd.addEventListener("input", () => {
    updateCharCount();
    updateActionButtonsState();
  });

  inputCompany.addEventListener("input", () => {
    updateJobSummary();
    const comp = inputCompany.value.trim();
    if (comp) {
      lookupAndRenderCompany(comp, inputTitle.value.trim(), currentActiveUrl);
    } else if (companyIntelligenceBar) {
      companyIntelligenceBar.classList.add("hidden");
    }
  });

  inputTitle.addEventListener("input", () => {
    updateJobSummary();
  });

  if (btnToggleJd) {
    btnToggleJd.addEventListener("click", () => toggleJdCompact());
  }

  // Edit company/role toggle
  if (btnEditJobMeta && jobMetaInputs) {
    btnEditJobMeta.addEventListener("click", () => {
      jobMetaInputs.classList.toggle("hidden");
      if (!jobMetaInputs.classList.contains("hidden")) {
        inputCompany.focus();
      }
    });
  }

  // Header overflow menu toggle
  if (btnMenuOverflow && overflowMenu) {
    btnMenuOverflow.addEventListener("click", (e) => {
      e.stopPropagation();
      overflowMenu.classList.toggle("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!btnMenuOverflow.contains(e.target)) {
        overflowMenu.classList.add("hidden");
      }
    });
  }

  if (btnViewMasterPdf) {
    btnViewMasterPdf.addEventListener("click", (e) => {
      e.preventDefault();
      if (overflowMenu) overflowMenu.classList.add("hidden");
      openUrlInTab(`${BRIDGE_URL}/view/master_resume.pdf`);
    });
  }

  if (btnReextract) {
    btnReextract.addEventListener("click", () => {
      if (overflowMenu) overflowMenu.classList.add("hidden");
      loadJobFromActiveTab();
    });
  }

  btnRetryBridge.addEventListener("click", checkBridgeHealth);

  // Actions
  btnEvaluate.addEventListener("click", () => handleEvaluate());
  btnTailor.addEventListener("click", () => handleTailor());
  if (btnOutreach) btnOutreach.addEventListener("click", () => handleGenerateOutreach());
  if (btnAutofill) btnAutofill.addEventListener("click", () => handleAutofillForm());
  if (btnDismissMain) btnDismissMain.addEventListener("click", () => handleDismissJob());
  if (btnDismissEval) btnDismissEval.addEventListener("click", () => handleDismissJob());

  // Direct ATS Link
  if (btnDirectAts) {
    btnDirectAts.addEventListener("click", (e) => {
      e.preventDefault();
      const href = btnDirectAts.getAttribute("data-url") || btnDirectAts.href;
      if (href) openUrlInTab(href);
    });
  }

  // Tailor actions
  btnRefine.addEventListener("click", () => handleRefine());
  btnConfirmDownload.addEventListener("click", () => handleDownloadResume());
  btnViewDiff.addEventListener("click", () => {
    diffContainer.classList.toggle("hidden");
  });
  btnMarkApplied.addEventListener("click", () => handleMarkApplied());

  if (btnOpenFullTab) {
    btnOpenFullTab.addEventListener("click", (e) => {
      e.preventDefault();
      const targetUrl = currentViewUrl || (currentFilename ? `${BRIDGE_URL}/view/${encodeURIComponent(currentFilename)}` : "");
      if (targetUrl) {
        openUrlInTab(targetUrl);
      } else {
        showToast("Tailor your resume first to generate a full preview.", "info");
      }
    });
  }

  // Outreach Studio events
  if (btnSearchRecruiters) {
    btnSearchRecruiters.addEventListener("click", (e) => {
      e.preventDefault();
      const comp = inputCompany.value.trim() || "Technology";
      const searchUrl = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(comp + " recruiter")}`;
      openUrlInTab(searchUrl);
    });
  }

  if (outreachBodyTextarea) {
    outreachBodyTextarea.addEventListener("input", updateOutreachCharCount);
  }

  if (tabOutreachLinkedin) tabOutreachLinkedin.addEventListener("click", () => switchOutreachFormat("linkedin"));
  if (tabOutreachEmail) tabOutreachEmail.addEventListener("click", () => switchOutreachFormat("email"));
  if (tabOutreachReferral) tabOutreachReferral.addEventListener("click", () => switchOutreachFormat("referral"));

  if (audiencePills && audiencePills.length > 0) {
    audiencePills.forEach((pill) => {
      pill.addEventListener("click", async () => {
        audiencePills.forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
        currentAudience = pill.getAttribute("data-audience") || "recruiter";
        await handleGenerateOutreach();
      });
    });
  }

  if (btnCopyOutreach) btnCopyOutreach.addEventListener("click", () => handleCopyOutreach());
  if (btnCopySubject) btnCopySubject.addEventListener("click", () => handleCopySubject());

  // Screening assistant events
  if (btnCloseScreening) {
    btnCloseScreening.addEventListener("click", () => {
      screeningCard.classList.add("hidden");
      updateEmptyStateVisibility();
    });
  }

  if (chipBtns && chipBtns.length > 0) {
    chipBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const q = btn.getAttribute("data-q") || btn.textContent;
        if (screeningQuestionInput) screeningQuestionInput.value = q;
        handleAnswerScreeningQuestion(q);
      });
    });
  }

  if (btnGenerateAnswer) {
    btnGenerateAnswer.addEventListener("click", () => {
      const q = screeningQuestionInput ? screeningQuestionInput.value.trim() : "";
      if (!q) {
        showToast("Please enter or pick a screening question.", "info");
        return;
      }
      handleAnswerScreeningQuestion(q);
    });
  }

  if (screeningQuestionInput) {
    screeningQuestionInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (btnGenerateAnswer) btnGenerateAnswer.click();
      }
    });
  }

  if (btnCopyScreening) btnCopyScreening.addEventListener("click", () => handleCopyScreeningAnswer());
  if (btnInsertScreening) btnInsertScreening.addEventListener("click", () => handleInsertScreeningAnswer());

  // Tab switch load
  if (btnTabSwitchLoad) {
    btnTabSwitchLoad.addEventListener("click", async () => {
      if (tabSwitchBanner) tabSwitchBanner.classList.add("hidden");
      currentEvaluation = null;
      currentOutreachData = null;
      inputCompany.value = "";
      inputTitle.value = "";
      inputJd.value = "";
      if (companyIntelligenceBar) companyIntelligenceBar.classList.add("hidden");
      evalCard.classList.add("hidden");
      if (outreachCard) outreachCard.classList.add("hidden");
      if (screeningCard) screeningCard.classList.add("hidden");
      if (autofillBanner) autofillBanner.classList.add("hidden");
      tailorCard.classList.add("hidden");
      await loadJobFromActiveTab();
      updateEmptyStateVisibility();
    });
  }

  // Side Panel tab activation
  if (chrome.tabs && chrome.tabs.onActivated) {
    chrome.tabs.onActivated.addListener(async (activeInfo) => {
      await handleTabActivated(activeInfo.tabId);
    });
  }
}

function openUrlInTab(url) {
  if (chrome.tabs && chrome.tabs.create) {
    chrome.tabs.create({ url: url, active: true });
  } else {
    window.open(url, "_blank");
  }
}

function updateCharCount() {
  const len = inputJd.value.trim().length;
  if (jdCharCount) jdCharCount.textContent = `${len.toLocaleString()} chars`;
  updateJobSummary();
}

function updateJobSummary() {
  const comp = (inputCompany.value || "").trim();
  const role = (inputTitle.value || "").trim();
  const len = inputJd.value.trim().length;

  if (summaryCompany) {
    summaryCompany.textContent = comp || "No company detected";
  }
  if (summaryTitle) {
    summaryTitle.textContent = role || "No role detected";
  }

  if (toggleJdText) {
    if (len > 0) {
      toggleJdText.textContent = `Job description captured · ${len.toLocaleString()} chars`;
    } else {
      toggleJdText.textContent = "Job description";
    }
  }

  if (btnSearchRecruiters && comp) {
    btnSearchRecruiters.href = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(comp + " recruiter")}`;
  }
}

function updateActionButtonsState() {
  const hasJd = inputJd.value.trim().length > 0;
  if (btnEvaluate) {
    btnEvaluate.disabled = !hasJd;
    btnEvaluate.setAttribute("aria-disabled", String(!hasJd));
  }
  if (btnTailor) {
    btnTailor.disabled = !hasJd;
    btnTailor.setAttribute("aria-disabled", String(!hasJd));
  }
  if (btnOutreach) {
    btnOutreach.disabled = !hasJd;
    btnOutreach.setAttribute("aria-disabled", String(!hasJd));
  }
}

function updateEmptyStateVisibility() {
  if (!emptyState) return;
  const isEvaluating = loadingCard && !loadingCard.classList.contains("hidden");
  const hasEval = evalCard && !evalCard.classList.contains("hidden");
  const hasOutreach = outreachCard && !outreachCard.classList.contains("hidden");
  const hasScreening = screeningCard && !screeningCard.classList.contains("hidden");
  const hasTailor = tailorCard && !tailorCard.classList.contains("hidden");

  if (isEvaluating || hasEval || hasOutreach || hasScreening || hasTailor) {
    emptyState.classList.add("hidden");
  } else {
    emptyState.classList.remove("hidden");
  }
}

function toggleJdCompact(forceCompact) {
  if (!jdTextareaWrap) return;
  const isCompact = typeof forceCompact === "boolean"
    ? forceCompact
    : !jdTextareaWrap.classList.contains("hidden");

  if (isCompact) {
    jdTextareaWrap.classList.add("hidden");
    if (toggleJdIcon) toggleJdIcon.classList.add("chevron-rotated");
  } else {
    jdTextareaWrap.classList.remove("hidden");
    if (toggleJdIcon) toggleJdIcon.classList.remove("chevron-rotated");
  }
}

// 1. Check Local Bridge Health
async function checkBridgeHealth() {
  setBridgeBadge("checking", "Checking...");
  bridgeWarning.classList.add("hidden");

  try {
    const res = await fetch(`${BRIDGE_URL}/health`, { method: "GET" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    setBridgeBadge("online", "Online");
    if (footerDbStats && data.database) {
      footerDbStats.textContent = `Radar DB: ${data.database.active_jobs} active (${data.database.applied_jobs} applied)`;
    }
    return true;
  } catch (err) {
    setBridgeBadge("offline", "Offline");
    bridgeWarning.classList.remove("hidden");
    return false;
  }
}

function setBridgeBadge(status, text) {
  bridgeStatus.className = `status-badge status-${status}`;
  bridgeStatus.querySelector(".status-text").textContent = text;
}

// 2. Extract Job Details from Active Tab
async function loadJobFromActiveTab() {
  if (tabSwitchBanner) tabSwitchBanner.classList.add("hidden");
  try {
    const stored = await chrome.storage.local.get(["selected_jd_text", "page_url", "page_title"]);
    if (stored.selected_jd_text) {
      inputJd.value = stored.selected_jd_text;
      currentActiveUrl = stored.page_url || "";
      if (stored.page_title) {
        guessCompanyAndRole(stored.page_title);
      }
      updateCharCount();
      updateJobSummary();
      updateActionButtonsState();
      if (inputCompany.value) {
        lookupAndRenderCompany(inputCompany.value.trim(), inputTitle.value.trim(), currentActiveUrl);
      }
      chrome.storage.local.remove(["selected_jd_text"]);
      return;
    }

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) return;

    currentActiveUrl = tab.url || "";

    chrome.tabs.sendMessage(tab.id, { action: "extract_job_data" }, (response) => {
      if (chrome.runtime.lastError || !response || !response.success) {
        if (tab.title) guessCompanyAndRole(tab.title);
        updateJobSummary();
        updateActionButtonsState();
        return;
      }

      const data = response.data;
      if (data.company && !inputCompany.value) inputCompany.value = data.company;
      if (data.title && !inputTitle.value) inputTitle.value = data.title;
      if (data.jd_text) {
        inputJd.value = data.jd_text;
        updateCharCount();
      }
      updateJobSummary();
      updateActionButtonsState();

      if (inputCompany.value) {
        lookupAndRenderCompany(inputCompany.value.trim(), inputTitle.value.trim(), currentActiveUrl);
      }

      // If page auto-extracted a long JD, collapse textarea by default to keep screen compact
      if (inputJd.value.trim().length > 300) {
        toggleJdCompact(true);
      }
    });
  } catch (e) {
    console.warn("Could not query tab:", e);
  }
}

// 2b. Lookup Company Radar Intelligence & Direct ATS Portal
async function lookupAndRenderCompany(company, title, url) {
  if (!company || !companyIntelligenceBar) return;
  if (btnSearchRecruiters) {
    btnSearchRecruiters.href = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(company + " recruiter")}`;
  }

  try {
    const queryUrl = `${BRIDGE_URL}/lookup_company?company=${encodeURIComponent(company)}&title=${encodeURIComponent(title || "")}&url=${encodeURIComponent(url || currentActiveUrl || "")}`;
    const res = await fetch(queryUrl, { method: "GET" });
    if (!res.ok) return;

    const data = await res.json();
    if (!data || !data.found) {
      companyIntelligenceBar.classList.add("hidden");
      return;
    }

    const badgeType = data.badge_type || (data.monitored ? "monitored" : "unmonitored");
    ciStatusBadge.className = `ci-badge ci-badge-${badgeType}`;
    ciStatusBadge.textContent = data.badge_label || (data.monitored ? "Monitored GCC" : "Unmonitored");
    ciHistoryText.textContent = data.history_text || "";

    if (data.direct_ats_url) {
      btnDirectAts.setAttribute("data-url", data.direct_ats_url);
      btnDirectAts.href = data.direct_ats_url;
      if (btnDirectAtsLabel) {
        btnDirectAtsLabel.textContent = data.direct_ats_label || "Official ATS";
      }
      btnDirectAts.classList.remove("hidden");
    } else {
      btnDirectAts.classList.add("hidden");
    }

    companyIntelligenceBar.classList.remove("hidden");
  } catch (err) {
    console.debug("Company lookup skipped:", err);
  }
}

async function handleTabActivated(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab || !tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
      if (tabSwitchBanner) tabSwitchBanner.classList.add("hidden");
      return;
    }

    if (!inputJd.value.trim()) {
      await loadJobFromActiveTab();
      return;
    }

    if (currentActiveUrl && tab.url === currentActiveUrl) {
      if (tabSwitchBanner) tabSwitchBanner.classList.add("hidden");
      return;
    }

    if (tabSwitchBanner && tabSwitchTitle) {
      tabSwitchTitle.textContent = tab.title ? `Switch: ${tab.title.slice(0, 32)}...` : "New tab open";
      tabSwitchBanner.classList.remove("hidden");
    }
  } catch (e) {
    console.debug("Tab switch listener:", e);
  }
}

function guessCompanyAndRole(title) {
  const clean = title.replace(/\s*[-–|•]\s*(LinkedIn|Indeed|Naukri|Wellfound|Instahyre|Ashby|Lever|Greenhouse|Workday).*/i, "").trim();
  const parts = clean.split(/\s*[-–|:]\s*/);

  if (parts.length >= 2) {
    if (!inputCompany.value) inputCompany.value = parts[0].trim();
    if (!inputTitle.value) inputTitle.value = parts.slice(1).join(" - ").trim();
  } else if (!inputTitle.value) {
    inputTitle.value = clean;
  }
  updateJobSummary();
}

// 3. Handle Evaluate Fit
async function handleEvaluate() {
  const jd_text = inputJd.value.trim();
  if (!jd_text) {
    showToast("Please provide job description text.", "info");
    return;
  }

  showLoading("Evaluating fit against candidate profile...");
  evalCard.classList.add("hidden");
  if (outreachCard) outreachCard.classList.add("hidden");
  if (screeningCard) screeningCard.classList.add("hidden");

  try {
    const res = await fetch(`${BRIDGE_URL}/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jd_text: jd_text,
        company: inputCompany.value.trim(),
        title: inputTitle.value.trim(),
        url: currentActiveUrl,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentEvaluation = data;

    if (!inputCompany.value && data.company) inputCompany.value = data.company;
    if (!inputTitle.value && data.title) inputTitle.value = data.title;
    updateJobSummary();

    renderEvaluation(data);
  } catch (err) {
    showToast(`Evaluation failed: ${err.message}`, "bad");
  } finally {
    hideLoading();
    updateEmptyStateVisibility();
  }
}

function renderEvaluation(data) {
  const scoreNum = Math.max(0, Math.min(100, Number(data.score) || 0));
  scoreVal.textContent = scoreNum;

  let verdictText = (data.verdict || "BORDERLINE").toUpperCase();
  let verdictClass = "verdict-borderline";
  let strokeColor = "var(--warn)";

  if (scoreNum >= 75) {
    verdictText = "Strong Fit";
    verdictClass = "verdict-apply";
    strokeColor = "var(--good)";
  } else if (scoreNum >= 50) {
    verdictText = "Partial Fit";
    verdictClass = "verdict-borderline";
    strokeColor = "var(--warn)";
  } else {
    verdictText = "Weak Fit";
    verdictClass = "verdict-dismissed";
    strokeColor = "var(--bad)";
  }

  verdictBadge.textContent = verdictText;
  verdictBadge.className = `verdict-badge ${verdictClass}`;

  // Radial score ring
  if (gaugeFill) {
    const circumference = 201; // 2 * pi * 32
    const offset = circumference - (scoreNum / 100) * circumference;
    gaugeFill.style.strokeDashoffset = offset;
    gaugeFill.style.stroke = strokeColor;
  }

  oneLineReason.textContent = data.one_line_reason || "Evaluated against profile.";
  applyAction.textContent = data.apply_action || "";

  // Matched Skills
  matchedSkills.innerHTML = "";
  if (data.matched_skills && data.matched_skills.length > 0) {
    data.matched_skills.forEach((s) => {
      const pill = document.createElement("span");
      pill.className = "pill pill-match";
      pill.textContent = s;
      matchedSkills.appendChild(pill);
    });
    matchedSkillsContainer.classList.remove("hidden");
  } else {
    matchedSkillsContainer.classList.add("hidden");
  }

  // Missing Skills
  missingSkills.innerHTML = "";
  if (data.missing_skills && data.missing_skills.length > 0) {
    data.missing_skills.forEach((s) => {
      const pill = document.createElement("span");
      pill.className = "pill pill-missing";
      pill.textContent = s;
      missingSkills.appendChild(pill);
    });
    missingSkillsContainer.classList.remove("hidden");
  } else {
    missingSkillsContainer.classList.add("hidden");
  }

  // Green Flags
  greenFlagsList.innerHTML = "";
  if (data.green_flags && data.green_flags.length > 0) {
    data.green_flags.forEach((f) => {
      const li = document.createElement("li");
      li.textContent = f;
      greenFlagsList.appendChild(li);
    });
    greenFlagsContainer.classList.remove("hidden");
  } else {
    greenFlagsContainer.classList.add("hidden");
  }

  // Hard Blocks
  hardBlocksList.innerHTML = "";
  if (data.hard_blocks && data.hard_blocks.length > 0) {
    data.hard_blocks.forEach((b) => {
      const li = document.createElement("li");
      li.textContent = b;
      hardBlocksList.appendChild(li);
    });
    hardBlocksContainer.classList.remove("hidden");
  } else {
    hardBlocksContainer.classList.add("hidden");
  }

  // Warnings
  warningsList.innerHTML = "";
  if (data.warnings && data.warnings.length > 0) {
    data.warnings.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      warningsList.appendChild(li);
    });
    warningsContainer.classList.remove("hidden");
  } else {
    warningsContainer.classList.add("hidden");
  }

  evalCard.classList.remove("hidden");
  updateEmptyStateVisibility();
}

// 4. Handle Tailor & Compile PDF
async function handleTailor() {
  const jd_text = inputJd.value.trim();
  const company = inputCompany.value.trim() || "Target_Company";
  const role = inputTitle.value.trim() || "Software_Engineer";

  if (!jd_text) {
    showToast("Please provide job description text.", "info");
    return;
  }

  showLoading("Tailoring resume bullets & compiling PDF (Tectonic)...");
  tailorCard.classList.add("hidden");
  hideRefineStatus();

  try {
    const res = await fetch(`${BRIDGE_URL}/tailor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jd_text: jd_text,
        company: company,
        role: role,
        profile_type: "java_backend",
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentFilename = data.filename;
    currentDownloadUrl = data.download_url;
    currentViewUrl = data.view_url || `${BRIDGE_URL}/view/${encodeURIComponent(data.filename)}`;

    tailorFilename.textContent = data.filename;
    pdfPreviewFrame.src = `${currentViewUrl}?t=${Date.now()}`;
    btnOpenFullTab.href = currentViewUrl;

    if (data.diff) {
      diffContent.textContent = data.diff;
      btnViewDiff.classList.remove("hidden");
    }

    tailorCard.classList.remove("hidden");
    tailorCard.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showToast(`Tailoring failed: ${err.message}`, "bad");
  } finally {
    hideLoading();
    updateEmptyStateVisibility();
  }
}

// 5. Handle Refine Feedback
async function handleRefine() {
  const feedback = inputFeedback.value.trim();
  if (!feedback) {
    showToast("Please enter suggestion notes to refine your resume.", "info");
    return;
  }

  showRefineStatus("Refining with Groq & compiling preview...");

  try {
    const res = await fetch(`${BRIDGE_URL}/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentFilename,
        feedback: feedback,
        jd_text: inputJd.value.trim(),
        company: inputCompany.value.trim() || "Company",
        role: inputTitle.value.trim() || "Software_Engineer",
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentFilename = data.filename;
    currentDownloadUrl = data.download_url;
    currentViewUrl = data.view_url || `${BRIDGE_URL}/view/${encodeURIComponent(data.filename)}`;

    pdfPreviewFrame.src = `${currentViewUrl}?t=${Date.now()}`;
    btnOpenFullTab.href = currentViewUrl;

    if (data.diff) {
      diffContent.textContent = data.diff;
      diffContainer.classList.remove("hidden");
    }

    showRefineStatus("Changes applied & recompiled!");
    inputFeedback.value = "";
    showToast("Resume refined successfully!", "good");
  } catch (err) {
    showRefineStatus(`Refinement error: ${err.message}`);
    showToast(`Refinement failed: ${err.message}`, "bad");
  }
}

function showRefineStatus(msg) {
  refineStatus.textContent = msg;
  refineStatus.classList.remove("hidden");
}

function hideRefineStatus() {
  refineStatus.classList.add("hidden");
}

// 6. Handle Download
function handleDownloadResume() {
  if (!currentDownloadUrl) {
    showToast("Please tailor your resume before downloading.", "info");
    return;
  }

  const filename = currentFilename || "Chinmay_Maheshwari_Resume.pdf";
  if (chrome.downloads && chrome.downloads.download) {
    chrome.downloads.download({
      url: currentDownloadUrl,
      filename: filename,
      saveAs: true,
    });
  } else {
    const a = document.createElement("a");
    a.href = currentDownloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  showToast("Download started!", "good");
}

// 7. Handle Mark Applied in Radar DB
async function handleMarkApplied() {
  const company = inputCompany.value.trim();
  const title = inputTitle.value.trim();

  if (!company || !title) {
    showToast("Company and Role title required to log in Radar DB.", "info");
    return;
  }

  try {
    const res = await fetch(`${BRIDGE_URL}/record_job`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company: company,
        title: title,
        url: currentActiveUrl,
        status: "APPLIED",
        score: currentEvaluation ? currentEvaluation.score : 80,
        notes: `Tailored and applied via Chrome Extension on ${new Date().toLocaleDateString()}`,
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    btnMarkApplied.textContent = "Applied Logged";
    btnMarkApplied.disabled = true;
    showToast(`Marked ${company} as applied!`, "good");
    await lookupAndRenderCompany(company, title, currentActiveUrl);
  } catch (err) {
    showToast(`Failed to record application: ${err.message}`, "bad");
  }
}

// 8. Handle Dismiss Job
async function handleDismissJob() {
  const company = inputCompany.value.trim();
  const title = inputTitle.value.trim() || "All Roles";

  if (!company) {
    showToast("Please enter or extract company name before dismissing.", "info");
    return;
  }

  try {
    const reason = (currentEvaluation && currentEvaluation.one_line_reason)
      ? currentEvaluation.one_line_reason
      : "Dismissed by user via Chrome Extension";

    const res = await fetch(`${BRIDGE_URL}/dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company: company,
        title: title,
        url: currentActiveUrl,
        reason: reason,
        score: currentEvaluation ? currentEvaluation.score : 0,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    if (verdictBadge) {
      verdictBadge.textContent = "Dismissed";
      verdictBadge.className = "verdict-badge verdict-dismissed";
    }

    if (dismissBanner) {
      dismissBanner.textContent = `${company} dismissed from future Radar scans and alerts.`;
      dismissBanner.classList.remove("hidden");
    }

    showToast(`Dismissed ${company} from Radar`, "bad", () => {
      dismissBanner.classList.add("hidden");
      showToast(`Restored ${company} in Radar`, "good");
    });

    await lookupAndRenderCompany(company, title, currentActiveUrl);
    await checkBridgeHealth();
  } catch (err) {
    showToast(`Failed to dismiss job: ${err.message}`, "bad");
  }
}

// 9. Recruiter Outreach Studio
async function handleGenerateOutreach() {
  const jd_text = inputJd.value.trim();
  const company = inputCompany.value.trim() || "Target Company";
  const role = inputTitle.value.trim() || "Software Engineer";

  if (!jd_text && !company) {
    showToast("Provide JD or Company name to generate outreach.", "info");
    return;
  }

  showLoading(`Generating ${currentAudience.replace("_", " ")} outreach...`);

  try {
    const matched_skills = (currentEvaluation && currentEvaluation.matched_skills) ? currentEvaluation.matched_skills : [];

    const res = await fetch(`${BRIDGE_URL}/generate_outreach`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company: company,
        title: role,
        jd_text: jd_text,
        matched_skills: matched_skills,
        recipient_type: currentAudience,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentOutreachData = data;

    renderOutreachView();

    if (outreachCard) {
      outreachCard.classList.remove("hidden");
      outreachCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (err) {
    showToast(`Outreach generation failed: ${err.message}`, "bad");
  } finally {
    hideLoading();
    updateEmptyStateVisibility();
  }
}

function switchOutreachFormat(format) {
  currentOutreachFormat = format;

  if (tabOutreachLinkedin) tabOutreachLinkedin.classList.toggle("active", format === "linkedin");
  if (tabOutreachEmail) tabOutreachEmail.classList.toggle("active", format === "email");
  if (tabOutreachReferral) tabOutreachReferral.classList.toggle("active", format === "referral");

  renderOutreachView();
}

function renderOutreachView() {
  if (!currentOutreachData) return;
  const data = currentOutreachData;

  if (currentOutreachFormat === "email") {
    if (outreachSubjectRow) outreachSubjectRow.classList.remove("hidden");
    if (outreachSubjectInput) {
      outreachSubjectInput.value = (data.cold_email && data.cold_email.subject) ? data.cold_email.subject : "";
    }
  } else {
    if (outreachSubjectRow) outreachSubjectRow.classList.add("hidden");
  }

  let text = "";
  if (currentOutreachFormat === "linkedin") {
    text = data.linkedin_connection_note || "";
  } else if (currentOutreachFormat === "email") {
    text = (data.cold_email && data.cold_email.body) ? data.cold_email.body : "";
  } else if (currentOutreachFormat === "referral") {
    text = data.referral_request || "";
  }

  if (outreachBodyTextarea) {
    outreachBodyTextarea.value = text;
  }

  updateOutreachCharCount();
  if (copyOutreachText) {
    copyOutreachText.textContent = currentOutreachFormat === "linkedin" ? "Copy Note" : "Copy Message";
  }
}

function updateOutreachCharCount() {
  if (!outreachCharCount || !outreachBodyTextarea) return;
  const len = outreachBodyTextarea.value.length;

  if (currentOutreachFormat === "linkedin") {
    outreachCharCount.textContent = `${len} / 300 chars`;
    if (len > 300) {
      outreachCharCount.style.color = "var(--bad)";
    } else if (len >= 270) {
      outreachCharCount.style.color = "var(--warn)";
    } else {
      outreachCharCount.style.color = "var(--good)";
    }
  } else {
    outreachCharCount.textContent = `${len} chars`;
    outreachCharCount.style.color = "var(--text-muted)";
  }
}

async function handleCopyOutreach() {
  if (!outreachBodyTextarea) return;
  const text = outreachBodyTextarea.value;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    if (copyOutreachText) copyOutreachText.textContent = "Copied!";
    showToast("Outreach message copied to clipboard!", "good");
    setTimeout(() => {
      if (copyOutreachText) {
        copyOutreachText.textContent = currentOutreachFormat === "linkedin" ? "Copy Note" : "Copy Message";
      }
    }, 2000);
  } catch (err) {
    outreachBodyTextarea.select();
    document.execCommand("copy");
    showToast("Copied to clipboard!", "good");
  }
}

async function handleCopySubject() {
  if (!outreachSubjectInput) return;
  const text = outreachSubjectInput.value;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    if (btnCopySubject) btnCopySubject.textContent = "Copied!";
    showToast("Subject copied!", "good");
    setTimeout(() => {
      if (btnCopySubject) btnCopySubject.textContent = "Copy";
    }, 1500);
  } catch (err) {
    outreachSubjectInput.select();
    document.execCommand("copy");
  }
}

// 10. 1-Click ATS Form Autofill & Screening Assistant
async function fetchCandidateProfile() {
  if (candidateProfileCache) return candidateProfileCache;
  try {
    const res = await fetch(`${BRIDGE_URL}/candidate_profile`);
    if (res.ok) {
      const data = await res.json();
      if (data && data.profile) {
        candidateProfileCache = data.profile;
        return candidateProfileCache;
      }
    }
  } catch (e) {
    console.debug("Bridge profile fetch failed, using fallback:", e);
  }

  candidateProfileCache = {
    first_name: "Chinmay",
    last_name: "Maheshwari",
    full_name: "Chinmay Maheshwari",
    email: "chinmaymaheshwari.it27@gmail.com",
    phone: "+91 9460449962",
    linkedin: "https://www.linkedin.com/in/chinmay8064/",
    github: "https://github.com/Chinmay468",
    portfolio: "https://github.com/Chinmay468",
    city: "Jaipur",
    state: "Rajasthan",
    country: "India",
    location: "Jaipur, India",
    university: "Jaipur Engineering College and Research Centre",
    school: "Jaipur Engineering College and Research Centre",
    degree: "B.Tech in Information Technology",
    graduation_year: "2027",
    gpa: "8.8",
    notice_period: "Immediate",
    authorized_in_country: "Yes",
    visa_sponsorship_needed: "No",
    willing_to_relocate: "Yes",
    gender: "Male",
  };
  return candidateProfileCache;
}

async function handleAutofillForm() {
  showLoading("Detecting form fields & autofilling...");
  if (autofillBanner) autofillBanner.classList.add("hidden");

  try {
    const profile = await fetchCandidateProfile();
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      throw new Error("No active tab found.");
    }

    chrome.tabs.sendMessage(tab.id, { action: "autofill_form", profile: profile }, (response) => {
      hideLoading();
      if (chrome.runtime.lastError || !response || !response.success) {
        const errMsg = chrome.runtime.lastError
          ? chrome.runtime.lastError.message
          : (response && response.error ? response.error : "Content script unreachable on this page.");
        if (autofillBanner) {
          autofillBanner.textContent = `${errMsg} (Open a job application page)`;
          autofillBanner.classList.remove("hidden");
        }
        showToast("Open an application form to autofill.", "info");
        return;
      }

      const count = response.filled_count || 0;
      const fields = response.filled_fields || [];
      const questions = response.open_questions || [];

      if (autofillBanner) {
        if (count > 0) {
          autofillBanner.textContent = `${count} fields filled: ${fields.join(", ")}`;
        } else {
          autofillBanner.textContent = "No empty ATS fields detected on this page.";
        }
        autofillBanner.classList.remove("hidden");
      }

      showToast(`${count} fields autofilled!`, "good");

      if (questions.length > 0 && screeningCard) {
        screeningCard.classList.remove("hidden");
        const firstQ = questions[0];
        lastFocusedFieldId = firstQ.id;
        if (screeningQuestionInput && !screeningQuestionInput.value) {
          screeningQuestionInput.value = firstQ.context || "";
        }
        updateEmptyStateVisibility();
      }
    });
  } catch (err) {
    hideLoading();
    showToast(`Autofill failed: ${err.message}`, "bad");
  }
}

async function handleAnswerScreeningQuestion(questionText) {
  const q = questionText || (screeningQuestionInput ? screeningQuestionInput.value.trim() : "");
  if (!q) return;

  const company = inputCompany.value.trim() || "Target Company";
  const role = inputTitle.value.trim() || "Software Engineer";
  const jd_text = inputJd.value.trim();

  if (btnGenerateAnswer) {
    btnGenerateAnswer.disabled = true;
    btnGenerateAnswer.textContent = "Thinking...";
  }

  try {
    const res = await fetch(`${BRIDGE_URL}/answer_screening_question`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: q,
        company: company,
        role: role,
        jd_text: jd_text,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    if (screeningAnswerTextarea) {
      screeningAnswerTextarea.value = data.answer || "";
    }
    if (screeningSourceTag) {
      screeningSourceTag.textContent = data.source === "groq" ? "AI Generated" : "Profile Match";
    }
    if (screeningAnswerBox) {
      screeningAnswerBox.classList.remove("hidden");
    }
    if (screeningCard) {
      screeningCard.classList.remove("hidden");
      screeningCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (err) {
    showToast(`Failed to generate answer: ${err.message}`, "bad");
  } finally {
    if (btnGenerateAnswer) {
      btnGenerateAnswer.disabled = false;
      btnGenerateAnswer.textContent = "Answer";
    }
    updateEmptyStateVisibility();
  }
}

async function handleCopyScreeningAnswer() {
  if (!screeningAnswerTextarea) return;
  const text = screeningAnswerTextarea.value;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    if (copyScreeningText) copyScreeningText.textContent = "Copied!";
    showToast("Answer copied to clipboard!", "good");
    setTimeout(() => {
      if (copyScreeningText) copyScreeningText.textContent = "Copy";
    }, 2000);
  } catch (err) {
    screeningAnswerTextarea.select();
    document.execCommand("copy");
    showToast("Copied!", "good");
  }
}

async function handleInsertScreeningAnswer() {
  if (!screeningAnswerTextarea) return;
  const text = screeningAnswerTextarea.value;
  if (!text) return;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;

  chrome.tabs.sendMessage(tab.id, {
    action: "fill_specific_field",
    fieldId: lastFocusedFieldId,
    value: text,
  }, (response) => {
    if (chrome.runtime.lastError || !response || !response.success) {
      showToast("Could not insert directly. Copied to clipboard instead!", "info");
      handleCopyScreeningAnswer();
    } else {
      showToast("Answer inserted into field!", "good");
    }
  });
}

function showLoading(msg) {
  loadingText.textContent = msg;
  loadingCard.classList.remove("hidden");
  updateEmptyStateVisibility();
}

function hideLoading() {
  loadingCard.classList.add("hidden");
  updateEmptyStateVisibility();
}

// Lightweight Toast Component
function showToast(message, type = "info", undoCallback = null) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  const textSpan = document.createElement("span");
  textSpan.textContent = message;
  toast.appendChild(textSpan);

  if (undoCallback) {
    const undoBtn = document.createElement("button");
    undoBtn.className = "btn-toast-undo";
    undoBtn.textContent = "Undo";
    undoBtn.type = "button";
    undoBtn.addEventListener("click", () => {
      toast.remove();
      undoCallback();
    });
    toast.appendChild(undoBtn);
  }

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 200ms ease";
    setTimeout(() => toast.remove(), 200);
  }, 3800);
}
