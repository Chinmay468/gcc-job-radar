/**
 * content.js — Smart Job Description Extractor for GCC Job Radar.
 * Extracts title, company, and JD text across LinkedIn, Wellfound, Indeed,
 * Naukri, Greenhouse, Lever, Workday, SmartRecruiters, Unstop, and generic pages.
 */

function cleanText(text) {
  if (!text) return "";
  return text
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n\s*\n+/g, "\n\n")
    .trim();
}

function extractFromLinkedIn() {
  const titleEl = document.querySelector(
    "h1.job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title, h1.t-24, h1.topcard__title, .job-details-jobs-unified-top-card__content--two-pane h1"
  );
  const companyEl = document.querySelector(
    ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name, a.topcard__org-name-link, .job-details-jobs-unified-top-card__content--two-pane a"
  );
  const descEl = document.querySelector(
    "#job-details, .jobs-description__content, .jobs-box__html-content, .jobs-description, .description__text"
  );

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: companyEl ? companyEl.innerText.trim() : "",
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromWellfound() {
  const titleEl = document.querySelector("[data-test='JobTitle'], h1.styles_title__");
  const companyEl = document.querySelector("[data-test='CompanyName'], h2.styles_name__");
  const descEl = document.querySelector("[data-test='JobDescription'], .styles_description__");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: companyEl ? companyEl.innerText.trim() : "",
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromGreenhouse() {
  const titleEl = document.querySelector(".app-title, h1.app-title, #header h1");
  const companyEl = document.querySelector(".company-name, #header .header-logo");
  const descEl = document.querySelector("#content, #main_fields, .body");

  let company = companyEl ? companyEl.innerText.trim() : "";
  if (!company) {
    const match = window.location.pathname.match(/boards\.greenhouse\.io\/([^\/]+)/);
    if (match) company = match[1].replace(/[-_]/g, " ");
  }

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: company,
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromLever() {
  const titleEl = document.querySelector(".posting-headline h2, h2");
  const descEl = document.querySelector(".section-wrapper.page-full-width, .content, .posting-page");

  let company = "";
  const match = window.location.hostname.match(/jobs\.lever\.co\/([^\/]+)/) || window.location.pathname.match(/\/([^\/]+)\/[a-f0-9\-]+/);
  if (match) company = match[1].replace(/[-_]/g, " ");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: company,
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromWorkday() {
  const titleEl = document.querySelector("[data-automation-id='jobPostingHeader'], h2");
  const descEl = document.querySelector("[data-automation-id='jobPostingDescription']");

  let company = "";
  const hostMatch = window.location.hostname.match(/([a-zA-Z0-9_\-]+)\.(my)?workdayjobs\.com/);
  if (hostMatch) company = hostMatch[1].replace(/[-_]/g, " ");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: company,
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromSmartRecruiters() {
  const titleEl = document.querySelector(".job-title, h1.job-title");
  const companyEl = document.querySelector(".company-name, .job-company");
  const descEl = document.querySelector(".job-sections, .job-detail, #st-jobDescription");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: companyEl ? companyEl.innerText.trim() : "",
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromIndeed() {
  const titleEl = document.querySelector(".jobsearch-JobInfoHeader-title, h1");
  const companyEl = document.querySelector("[data-testid='inlineHeader-companyName']");
  const descEl = document.querySelector("#jobDescriptionText");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: companyEl ? companyEl.innerText.trim() : "",
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromNaukri() {
  const titleEl = document.querySelector(".jd-header-title, h1");
  const companyEl = document.querySelector(".jd-header-comp-name, .comp-name");
  const descEl = document.querySelector(".styles_job-desc-container__txpYf, .job-desc, .dang-inner-html");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: companyEl ? companyEl.innerText.trim() : "",
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractGeneric() {
  // 1. Check if user selected text manually
  const selection = window.getSelection().toString().trim();
  if (selection && selection.length > 80) {
    let title = document.querySelector("h1")?.innerText.trim() || document.title;
    return {
      title: title.slice(0, 80),
      company: "",
      jd_text: cleanText(selection),
      selection_used: true,
    };
  }

  // 2. Try largest content block (<main>, <article>, or dense div)
  const candidateEls = Array.from(document.querySelectorAll("main, article, [role='main'], .job-description, .description, .content"));
  let bestEl = null;
  let bestLength = 0;

  for (const el of candidateEls) {
    const len = el.innerText.length;
    if (len > bestLength && len > 200) {
      bestLength = len;
      bestEl = el;
    }
  }

  if (!bestEl) {
    bestEl = document.body;
  }

  // Guess title & company from document.title
  let title = document.querySelector("h1")?.innerText.trim() || "";
  let company = "";
  const docTitle = document.title;
  
  if (docTitle.includes(" at ")) {
    const parts = docTitle.split(" at ");
    title = title || parts[0].trim();
    company = parts[1].split(/[|\-–]/)[0].trim();
  } else if (docTitle.includes(" - ")) {
    const parts = docTitle.split(" - ");
    title = title || parts[0].trim();
    company = parts[1].trim();
  } else if (docTitle.includes(" | ")) {
    const parts = docTitle.split(" | ");
    title = title || parts[0].trim();
    company = parts[1].trim();
  }

  return {
    title: title || docTitle.slice(0, 60),
    company: company,
    jd_text: cleanText(bestEl.innerText),
  };
}

function extractJobDetails() {
  const host = window.location.hostname.toLowerCase();
  let extracted = { title: "", company: "", jd_text: "" };

  if (host.includes("linkedin.com")) {
    extracted = extractFromLinkedIn();
  } else if (host.includes("wellfound.com") || host.includes("angel.co")) {
    extracted = extractFromWellfound();
  } else if (host.includes("greenhouse.io")) {
    extracted = extractFromGreenhouse();
  } else if (host.includes("lever.co")) {
    extracted = extractFromLever();
  } else if (host.includes("workdayjobs.com")) {
    extracted = extractFromWorkday();
  } else if (host.includes("smartrecruiters.com")) {
    extracted = extractFromSmartRecruiters();
  } else if (host.includes("indeed.com")) {
    extracted = extractFromIndeed();
  } else if (host.includes("naukri.com")) {
    extracted = extractFromNaukri();
  }

  // If specialized extractor failed or yielded too little text, use generic fallback
  if (!extracted.jd_text || extracted.jd_text.length < 150) {
    const generic = extractGeneric();
    extracted.title = extracted.title || generic.title;
    extracted.company = extracted.company || generic.company;
    extracted.jd_text = extracted.jd_text && extracted.jd_text.length > generic.jd_text.length
      ? extracted.jd_text
      : generic.jd_text;
    extracted.selection_used = generic.selection_used || false;
  }

  return {
    ...extracted,
    url: window.location.href,
    domain: host,
  };
}

// Listen for message from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "extract_job_data") {
    try {
      const data = extractJobDetails();
      sendResponse({ success: true, data: data });
    } catch (err) {
      sendResponse({ success: false, error: err.message });
    }
  }
  return true; // Keep channel open for async response
});
