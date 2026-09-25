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

const btnEvaluate = document.getElementById("btn-evaluate");
const btnTailor = document.getElementById("btn-tailor");

const loadingCard = document.getElementById("loading-card");
const loadingText = document.getElementById("loading-text");

// Eval card elements
const evalCard = document.getElementById("eval-card");
const verdictBadge = document.getElementById("verdict-badge");
const scoreVal = document.getElementById("score-val");
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
  btnRetryBridge.addEventListener("click", checkBridgeHealth);
  btnReextract.addEventListener("click", loadJobFromActiveTab);
  btnEvaluate.addEventListener("click", handleEvaluate);
  btnTailor.addEventListener("click", handleTailor);
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
      if (currentViewUrl) {
        openUrlInTab(currentViewUrl);
      }
    });
  }
}

function openUrlInTab(url) {
  if (chrome.tabs && chrome.tabs.create) {
    chrome.tabs.create({ url: url });
  } else {
    window.open(url, "_blank");
  }
}

function updateCharCount() {
  const len = inputJd.value.trim().length;
  jdCharCount.textContent = `${len.toLocaleString()} chars`;
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
    });
  } catch (e) {
    console.warn("Could not query tab:", e);
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

  oneLineReason.textContent = data.one_line_reason || "Evaluated against candidate profile.";
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
    setTimeout(() => {
      btnMarkApplied.textContent = "✅ Applied in Radar DB";
    }, 2000);
  } catch (err) {
    alert(`Failed to record application: ${err.message}`);
  }
}

function showLoading(msg) {
  loadingText.textContent = msg;
  loadingCard.classList.remove("hidden");
}

function hideLoading() {
  loadingCard.classList.add("hidden");
}
