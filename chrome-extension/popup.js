/**
 * popup.js — GCC Job Radar & Resume Tailor
 * Connects browser tab data to local Bridge daemon on http://127.0.0.1:8765
 */

const BRIDGE_URL = "http://127.0.0.1:8765";

// DOM Elements
const bridgeStatus = document.getElementById("bridge-status");
const bridgeWarning = document.getElementById("bridge-warning");
const btnRetryBridge = document.getElementById("btn-retry-bridge");

const inputCompany = document.getElementById("input-company");
const inputTitle = document.getElementById("input-title");
const inputJd = document.getElementById("input-jd");
const jdCharCount = document.getElementById("jd-char-count");
const btnReextract = document.getElementById("btn-reextract");
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

// Side Panel tab switch elements
const tabSwitchBanner = document.getElementById("tab-switch-banner");
const tabSwitchTitle = document.getElementById("tab-switch-title");
const btnTabSwitchLoad = document.getElementById("btn-tab-switch-load");

const btnEvaluate = document.getElementById("btn-evaluate");
const btnTailor = document.getElementById("btn-tailor");
const btnDismissMain = document.getElementById("btn-dismiss-main");

const loadingCard = document.getElementById("loading-card");
const loadingText = document.getElementById("loading-text");

// Eval card elements
const evalCard = document.getElementById("eval-card");
const verdictBadge = document.getElementById("verdict-badge");
const btnDismissEval = document.getElementById("btn-dismiss-eval");
const dismissBanner = document.getElementById("dismiss-banner");
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

// Master Resume Preview
const btnViewMasterPdf = document.getElementById("btn-view-master-pdf");

// Tailor & Preview card elements
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

const footerDbStats = document.getElementById("footer-db-stats");

let currentActiveUrl = "";
let currentEvaluation = null;
let currentFilename = "";
let currentDownloadUrl = "";
let currentViewUrl = "";
let currentVersion = 1;

// Initialize on DOM ready
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  await checkBridgeHealth();
  await loadJobFromActiveTab();
});

function setupEventListeners() {
  inputJd.addEventListener("input", updateCharCount);
  if (btnToggleJd) btnToggleJd.addEventListener("click", () => toggleJdCompact());
  btnRetryBridge.addEventListener("click", checkBridgeHealth);
  btnReextract.addEventListener("click", loadJobFromActiveTab);
  btnEvaluate.addEventListener("click", handleEvaluate);
  btnTailor.addEventListener("click", handleTailor);
  if (btnDismissMain) btnDismissMain.addEventListener("click", handleDismissJob);
  if (btnDismissEval) btnDismissEval.addEventListener("click", handleDismissJob);
  btnRefine.addEventListener("click", handleRefine);
  btnConfirmDownload.addEventListener("click", handleDownloadResume);
  btnViewDiff.addEventListener("click", () => {
    diffContainer.classList.toggle("hidden");
  });
  btnMarkApplied.addEventListener("click", handleMarkApplied);

  if (btnViewMasterPdf) {
    btnViewMasterPdf.addEventListener("click", (e) => {
      e.preventDefault();
      openUrlInTab(`${BRIDGE_URL}/view/master_resume.pdf`);
    });
  }

  if (btnOpenFullTab) {
    btnOpenFullTab.addEventListener("click", (e) => {
      e.preventDefault();
      const targetUrl = currentViewUrl || (currentFilename ? `${BRIDGE_URL}/view/${encodeURIComponent(currentFilename)}` : "");
      if (targetUrl) {
        openUrlInTab(targetUrl);
      } else {
        alert("Please click 'Tailor Resume' first to compile and view your resume.");
      }
    });
  }

  if (inputCompany) {
    inputCompany.addEventListener("input", () => {
      const comp = inputCompany.value.trim();
      if (comp) {
        lookupAndRenderCompany(comp, inputTitle.value.trim(), currentActiveUrl);
      } else if (companyIntelligenceBar) {
        companyIntelligenceBar.classList.add("hidden");
      }
    });
  }

  if (btnDirectAts) {
    btnDirectAts.addEventListener("click", (e) => {
      e.preventDefault();
      const href = btnDirectAts.getAttribute("data-url");
      if (href) openUrlInTab(href);
    });
  }

  if (btnTabSwitchLoad) {
    btnTabSwitchLoad.addEventListener("click", async () => {
      if (tabSwitchBanner) tabSwitchBanner.classList.add("hidden");
      currentEvaluation = null;
      inputCompany.value = "";
      inputTitle.value = "";
      inputJd.value = "";
      if (companyIntelligenceBar) companyIntelligenceBar.classList.add("hidden");
      evalCard.classList.add("hidden");
      tailorCard.classList.add("hidden");
      await loadJobFromActiveTab();
    });
  }

  // Side Panel mode: listen for active tab changes
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
  jdCharCount.textContent = `${len.toLocaleString()} chars`;
}

function toggleJdCompact(forceCompact) {
  if (!jdTextareaWrap) return;
  const isCompact = typeof forceCompact === "boolean"
    ? forceCompact
    : !jdTextareaWrap.classList.contains("compact-mode");

  if (isCompact) {
    jdTextareaWrap.classList.add("compact-mode");
    if (toggleJdText) toggleJdText.textContent = "Expand";
    if (toggleJdIcon) toggleJdIcon.textContent = "↕️";
  } else {
    jdTextareaWrap.classList.remove("compact-mode");
    if (toggleJdText) toggleJdText.textContent = "Collapse";
    if (toggleJdIcon) toggleJdIcon.textContent = "↕️";
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

    setBridgeBadge("online", "Bridge Online");
    if (footerDbStats && data.database) {
      footerDbStats.textContent = `Radar DB: ${data.database.active_jobs} active (${data.database.applied_jobs} applied)`;
    }
    return true;
  } catch (err) {
    setBridgeBadge("offline", "Bridge Offline");
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
    // Check if context menu saved a selection
    const stored = await chrome.storage.local.get(["selected_jd_text", "page_url", "page_title"]);
    if (stored.selected_jd_text) {
      inputJd.value = stored.selected_jd_text;
      currentActiveUrl = stored.page_url || "";
      if (stored.page_title) {
        guessCompanyAndRole(stored.page_title);
      }
      updateCharCount();
      if (inputCompany.value) {
        lookupAndRenderCompany(inputCompany.value.trim(), inputTitle.value.trim(), currentActiveUrl);
      }
      chrome.storage.local.remove(["selected_jd_text"]);
      return;
    }

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) return;

    currentActiveUrl = tab.url || "";

    // Send extract message to content script
    chrome.tabs.sendMessage(tab.id, { action: "extract_job_data" }, (response) => {
      if (chrome.runtime.lastError || !response || !response.success) {
        // Fallback: use tab title
        if (tab.title) guessCompanyAndRole(tab.title);
        return;
      }

      const data = response.data;
      if (data.company && !inputCompany.value) inputCompany.value = data.company;
      if (data.title && !inputTitle.value) inputTitle.value = data.title;
      if (data.jd_text) {
        inputJd.value = data.jd_text;
        updateCharCount();
      }
      if (inputCompany.value) {
        lookupAndRenderCompany(inputCompany.value.trim(), inputTitle.value.trim(), currentActiveUrl);
      }
    });
  } catch (e) {
    console.warn("Could not query tab:", e);
  }
}

// 2b. Lookup Company Radar Intelligence & Direct ATS Portal (Phase 2)
async function lookupAndRenderCompany(company, title, url) {
  if (!company || !companyIntelligenceBar) return;
  try {
    const queryUrl = `${BRIDGE_URL}/lookup_company?company=${encodeURIComponent(company)}&title=${encodeURIComponent(title || "")}&url=${encodeURIComponent(url || currentActiveUrl || "")}`;
    const res = await fetch(queryUrl, { method: "GET" });
    if (!res.ok) return;

    const data = await res.json();
    if (!data || !data.found) {
      companyIntelligenceBar.classList.add("hidden");
      return;
    }

    // Set badge style and label
    const badgeType = data.badge_type || (data.monitored ? "monitored" : "unmonitored");
    ciStatusBadge.className = `ci-badge ci-badge-${badgeType}`;
    ciStatusBadge.textContent = data.badge_label;
    ciHistoryText.textContent = data.history_text || "";

    // Set Direct ATS button
    if (data.direct_ats_url) {
      btnDirectAts.setAttribute("data-url", data.direct_ats_url);
      btnDirectAts.href = data.direct_ats_url;
      if (btnDirectAtsLabel) {
        btnDirectAtsLabel.textContent = data.direct_ats_label || "Official ATS";
      }

      if (data.is_3rd_party_aggregator && data.monitored) {
        btnDirectAts.classList.add("pulse-aggregator");
        btnDirectAts.title = "Direct official ATS board detected! Click to bypass third-party aggregator.";
      } else {
        btnDirectAts.classList.remove("pulse-aggregator");
        btnDirectAts.title = "Jump directly to official company career portal";
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

    // If current form has no JD text, seamlessly auto-load from the newly activated tab
    if (!inputJd.value.trim()) {
      await loadJobFromActiveTab();
      return;
    }

    // If currently already viewing this tab's URL, hide prompt
    if (currentActiveUrl && tab.url === currentActiveUrl) {
      if (tabSwitchBanner) tabSwitchBanner.classList.add("hidden");
      return;
    }

    // Active tab has a different job/page: show banner offering to load it
    if (tabSwitchBanner && tabSwitchTitle) {
      const displayTitle = (tab.title || "Job Posting").split(/[-|–]/)[0].trim().slice(0, 32);
      tabSwitchTitle.textContent = `New tab: "${displayTitle}"`;
      tabSwitchBanner.classList.remove("hidden");
    }
  } catch (e) {
    // Ignore tab query errors
  }
}

function guessCompanyAndRole(title) {
  if (title.includes(" at ")) {
    const parts = title.split(" at ");
    if (!inputTitle.value) inputTitle.value = parts[0].trim();
    if (!inputCompany.value) inputCompany.value = parts[1].split(/[|\-–]/)[0].trim();
  } else if (title.includes(" - ")) {
    const parts = title.split(" - ");
    if (!inputTitle.value) inputTitle.value = parts[0].trim();
    if (!inputCompany.value) inputCompany.value = parts[1].trim();
  }
  if (inputCompany.value) {
    lookupAndRenderCompany(inputCompany.value.trim(), inputTitle.value.trim(), currentActiveUrl);
  }
}

// 3. Handle Evaluate Fit
async function handleEvaluate() {
  const jd_text = inputJd.value.trim();
  if (!jd_text) {
    alert("Please provide job description text.");
    return;
  }

  showLoading("Evaluating JD with Groq AI & Profile Rules...");
  evalCard.classList.add("hidden");

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

    // Fill extracted company/title if empty
    if (!inputCompany.value && data.company) inputCompany.value = data.company;
    if (!inputTitle.value && data.title) inputTitle.value = data.title;

    renderEvaluation(data);
  } catch (err) {
    alert(`Evaluation failed: ${err.message}\nMake sure bridge is running.`);
  } finally {
    hideLoading();
  }
}

function renderEvaluation(data) {
  // Score & Verdict
  scoreVal.textContent = data.score;
  const verdict = (data.verdict || "BORDERLINE").toUpperCase();
  verdictBadge.textContent = verdict;
  verdictBadge.className = `verdict-badge verdict-${verdict.toLowerCase()}`;

  // Radial Circular Gauge Animation
  if (gaugeFill) {
    const circumference = 201; // 2 * pi * 32
    const scoreNum = Math.max(0, Math.min(100, Number(data.score) || 0));
    const offset = circumference - (scoreNum / 100) * circumference;
    gaugeFill.style.strokeDashoffset = offset;

    if (verdict === "APPLY") {
      gaugeFill.style.stroke = "#10b981";
      gaugeFill.style.filter = "drop-shadow(0 0 6px rgba(16, 185, 129, 0.45))";
    } else if (verdict === "BORDERLINE") {
      gaugeFill.style.stroke = "#f59e0b";
      gaugeFill.style.filter = "drop-shadow(0 0 6px rgba(245, 158, 11, 0.45))";
    } else if (verdict === "DISMISSED") {
      gaugeFill.style.stroke = "#64748b";
      gaugeFill.style.filter = "none";
    } else {
      gaugeFill.style.stroke = "#f43f5e";
      gaugeFill.style.filter = "drop-shadow(0 0 6px rgba(244, 63, 94, 0.45))";
    }
  }

  // Auto-compact long JD so score & evaluation are immediately visible
  if (inputJd.value.trim().length > 400) {
    toggleJdCompact(true);
  }

  oneLineReason.textContent = data.one_line_reason || "Evaluated against candidate profile.";
  applyAction.textContent = data.apply_action || "";

  // Matched Skills
  matchedSkills.innerHTML = "";
  if (data.matched_skills && data.matched_skills.length > 0) {
    data.matched_skills.forEach((s) => {
      const pill = document.createElement("span");
      pill.className = "pill pill-match";
      pill.innerHTML = `<span class="skills-icon">✔</span> ${s}`;
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
      pill.innerHTML = `<span class="skills-icon warning">⚠</span> ${s}`;
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
}

// 4. Handle Tailor & Compile PDF (Live Preview)
async function handleTailor() {
  const jd_text = inputJd.value.trim();
  const company = inputCompany.value.trim() || "Target_Company";
  const role = inputTitle.value.trim() || "Software_Engineer";

  if (!jd_text) {
    alert("Please provide job description text.");
    return;
  }

  showLoading("Tailoring LaTeX with Groq & Compiling PDF via Tectonic (~1.4s)...");
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
        compile: true,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentFilename = data.filename;
    currentDownloadUrl = data.download_url;
    currentViewUrl = data.view_url;
    currentVersion = 1;

    // Setup preview card
    previewStatusTitle.textContent = "Resume Preview (Tailored v1)";
    tailorFilename.textContent = data.filename;
    btnOpenFullTab.href = data.view_url;
    pdfPreviewFrame.src = `${data.view_url}?t=${Date.now()}`;

    // Diff view
    diffContent.textContent = data.diff || "No structural diff.";
    tailorCard.classList.remove("hidden");

    // Scroll into preview card
    tailorCard.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert(`Resume tailoring failed: ${err.message}\nMake sure Groq API key and Tectonic are available.`);
  } finally {
    hideLoading();
  }
}

// 5. Handle Iterative Refinement
async function handleRefine() {
  const feedback = inputFeedback.value.trim();
  if (!feedback) {
    showRefineStatus("Please describe what changes you want to make (e.g. emphasize a project or skill).", true);
    return;
  }

  if (!currentFilename) {
    showRefineStatus("Please click 'Tailor & Compile PDF' first to generate an initial resume.", true);
    return;
  }

  const jd_text = inputJd.value.trim();
  const company = inputCompany.value.trim() || "Target_Company";
  const role = inputTitle.value.trim() || "Software_Engineer";

  btnRefine.disabled = true;
  btnRefine.innerHTML = `<span class="spinner" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin:0 4px 0 0;"></span> Applying Changes (~2s)...`;
  hideRefineStatus();

  try {
    const res = await fetch(`${BRIDGE_URL}/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentFilename,
        feedback: feedback,
        jd_text: jd_text,
        company: company,
        role: role,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentVersion += 1;
    currentFilename = data.filename;
    currentDownloadUrl = data.download_url;
    currentViewUrl = data.view_url;

    // Refresh preview frame
    previewStatusTitle.textContent = `Resume Preview (Refined v${currentVersion})`;
    tailorFilename.textContent = data.filename;
    btnOpenFullTab.href = data.view_url;
    pdfPreviewFrame.src = `${data.view_url}?t=${Date.now()}`;

    // Update diff
    if (data.diff) {
      diffContent.textContent = data.diff;
    }

    inputFeedback.value = "";
    showRefineStatus(`✨ Changes applied and PDF recompiled! Check preview below. You can refine again or download.`);
  } catch (err) {
    showRefineStatus(`Refinement failed: ${err.message}`, true);
  } finally {
    btnRefine.disabled = false;
    btnRefine.innerHTML = `<span class="btn-icon">🔄</span> Refine & Refresh Preview`;
  }
}

// 6. Handle User-Confirmed Download
function handleDownloadResume() {
  if (!currentDownloadUrl) {
    alert("No compiled resume available to download.");
    return;
  }

  if (chrome.downloads && chrome.downloads.download) {
    chrome.downloads.download({
      url: currentDownloadUrl,
      filename: currentFilename,
      saveAs: false,
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        window.open(currentDownloadUrl, "_blank");
      }
    });
  } else {
    const a = document.createElement("a");
    a.href = currentDownloadUrl;
    a.download = currentFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  const prevText = btnConfirmDownload.innerHTML;
  btnConfirmDownload.innerHTML = `<span>✅ Download Started!</span>`;
  setTimeout(() => {
    btnConfirmDownload.innerHTML = prevText;
  }, 2500);
}

function showRefineStatus(msg, isError = false) {
  refineStatus.textContent = msg;
  refineStatus.className = `refine-status ${isError ? "error" : ""}`;
  refineStatus.classList.remove("hidden");
}

function hideRefineStatus() {
  refineStatus.classList.add("hidden");
}

// 7. Handle Mark Applied in Radar DB
async function handleMarkApplied() {
  const company = inputCompany.value.trim();
  const title = inputTitle.value.trim();

  if (!company || !title) {
    alert("Company and Role title required to log in Radar database.");
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
    const data = await res.json();

    btnMarkApplied.textContent = "✅ Applied Logged!";
    btnMarkApplied.disabled = true;
    await lookupAndRenderCompany(company, title, currentActiveUrl);
    setTimeout(() => {
      btnMarkApplied.textContent = "✅ Applied in Radar DB";
    }, 2000);
  } catch (err) {
    alert(`Failed to record application: ${err.message}`);
  }
}

// 8. Handle Dismiss Job & Company from Radar
async function handleDismissJob() {
  const company = inputCompany.value.trim();
  const title = inputTitle.value.trim() || "All Roles";

  if (!company) {
    alert("Please enter or extract the company name before dismissing.");
    return;
  }

  const confirmMsg = `Are you sure you want to dismiss "${company}"?\nThis will suppress alerts for this company across Telegram, scans, and daily digests.`;
  if (!confirm(confirmMsg)) {
    return;
  }

  const originalMainText = btnDismissMain ? btnDismissMain.innerHTML : "";
  const originalEvalText = btnDismissEval ? btnDismissEval.innerHTML : "";

  if (btnDismissMain) {
    btnDismissMain.disabled = true;
    btnDismissMain.innerHTML = `<span>⏳ Dismissing...</span>`;
  }
  if (btnDismissEval) {
    btnDismissEval.disabled = true;
    btnDismissEval.innerHTML = `⏳ Dismissing...`;
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

    const data = await res.json();

    // Visual feedback
    if (verdictBadge) {
      verdictBadge.textContent = "DISMISSED";
      verdictBadge.className = "verdict-badge verdict-dismissed";
    }

    if (gaugeFill) {
      gaugeFill.style.stroke = "#64748b";
      gaugeFill.style.filter = "none";
    }

    if (dismissBanner) {
      dismissBanner.innerHTML = `<span>🗑️</span><span><strong>${company}</strong> dismissed and suppressed from future radar scans & alerts.</span>`;
      dismissBanner.classList.remove("hidden");
    }

    if (btnDismissMain) {
      btnDismissMain.innerHTML = `<span class="btn-icon">✅</span> Dismissed`;
      btnDismissMain.classList.add("btn-disabled");
    }
    if (btnDismissEval) {
      btnDismissEval.innerHTML = `✅ Dismissed`;
      btnDismissEval.disabled = true;
    }

    // Refresh company status & bridge stats
    await lookupAndRenderCompany(company, title, currentActiveUrl);
    await checkBridgeHealth();

  } catch (err) {
    alert(`Failed to dismiss job: ${err.message}\nMake sure the local bridge is running.`);
    if (btnDismissMain) {
      btnDismissMain.disabled = false;
      btnDismissMain.innerHTML = originalMainText;
    }
    if (btnDismissEval) {
      btnDismissEval.disabled = false;
      btnDismissEval.innerHTML = originalEvalText;
    }
  }
}

function showLoading(msg) {
  loadingText.textContent = msg;
  loadingCard.classList.remove("hidden");
}

function hideLoading() {
  loadingCard.classList.add("hidden");
}
