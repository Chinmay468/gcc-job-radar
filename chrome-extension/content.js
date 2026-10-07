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

function extractFromAshby() {
  // Check JSON-LD first for crisp structured data
  const jsonLd = extractJsonLd();
  if (jsonLd && jsonLd.jd_text && jsonLd.jd_text.length > 100) {
    if (!jsonLd.company) {
      const match = window.location.pathname.match(/\/([^\/]+)\/[a-f0-9\-]+/i) ||
                    window.location.hostname.match(/([a-zA-Z0-9_\-]+)\.ashbyhq\.com/i);
      if (match) jsonLd.company = match[1].replace(/[-_]/g, " ");
    }
    return jsonLd;
  }

  const titleEl = document.querySelector(
    "h1.ashby-job-posting-heading, h1, [class*='JobPostingHeading'], [class*='heading_'], [class*='jobTitle']"
  );
  const companyEl = document.querySelector(
    ".ashby-job-posting-company-name, [class*='companyName'], [class*='CompanyHeader'], header img[alt]"
  );
  const descEl = document.querySelector(
    ".ashby-job-posting-description, [class*='JobPostingDescription'], [class*='description_'], [data-testid='job-description'], main"
  );

  let company = "";
  if (companyEl) {
    company = (companyEl.innerText || companyEl.getAttribute("alt") || "").trim();
  }
  if (!company) {
    const match = window.location.pathname.match(/\/([^\/]+)\/[a-f0-9\-]+/i) ||
                  window.location.hostname.match(/([a-zA-Z0-9_\-]+)\.ashbyhq\.com/i);
    if (match) company = match[1].replace(/[-_]/g, " ");
  }

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: company,
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractFromInstahyre() {
  const titleEl = document.querySelector(".job-title, h1, .profile-info h1, [class*='job-header'] h1");
  const companyEl = document.querySelector(".employer-name, .company-name, a.employer, [class*='employer']");
  const descEl = document.querySelector(".job-description, .description-content, #job-description, .profile-description, [class*='job-description']");

  return {
    title: titleEl ? titleEl.innerText.trim() : "",
    company: companyEl ? companyEl.innerText.trim() : "",
    jd_text: descEl ? cleanText(descEl.innerText) : "",
  };
}

function extractJsonLd() {
  try {
    const scripts = document.querySelectorAll("script[type='application/ld+json']");
    for (const s of scripts) {
      const raw = s.innerText.trim();
      if (!raw) continue;
      const parsed = JSON.parse(raw);
      const items = Array.isArray(parsed) ? parsed : (parsed["@graph"] ? parsed["@graph"] : [parsed]);
      for (const item of items) {
        if (item && item["@type"] === "JobPosting") {
          const tempDiv = document.createElement("div");
          tempDiv.innerHTML = item.description || "";
          const hiringOrg = item.hiringOrganization;
          const companyName = typeof hiringOrg === "string" ? hiringOrg : (hiringOrg && hiringOrg.name ? hiringOrg.name : "");
          return {
            title: item.title || "",
            company: companyName,
            jd_text: cleanText(tempDiv.innerText),
          };
        }
      }
    }
  } catch (e) {
    // Ignore JSON parse errors in malformed script tags
  }
  return null;
}

function extractJobDetails() {
  const host = window.location.hostname.toLowerCase();
  let extracted = { title: "", company: "", jd_text: "" };

  if (host.includes("ashbyhq.com")) {
    extracted = extractFromAshby();
  } else if (host.includes("linkedin.com")) {
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
  } else if (host.includes("instahyre.com")) {
    extracted = extractFromInstahyre();
  }

  // Attempt JSON-LD if specialized extractor yielded too little description
  if (!extracted.jd_text || extracted.jd_text.length < 150) {
    const jsonLd = extractJsonLd();
    if (jsonLd && jsonLd.jd_text && jsonLd.jd_text.length >= 150) {
      extracted.title = extracted.title || jsonLd.title;
      extracted.company = extracted.company || jsonLd.company;
      extracted.jd_text = jsonLd.jd_text;
    }
  }

  // If still too little text, use generic DOM / selection fallback
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

// Floating Quick Badge on Job Pages (Phase 2)
async function initFloatingRadarBadge() {
  if (document.getElementById("gcc-radar-badge-host")) return;

  setTimeout(async () => {
    try {
      const details = extractJobDetails();
      if (!details || !details.company) return;

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 1200);

      const queryUrl = `http://127.0.0.1:8765/lookup_company?company=${encodeURIComponent(details.company)}&title=${encodeURIComponent(details.title || "")}&url=${encodeURIComponent(window.location.href)}`;
      const res = await fetch(queryUrl, { signal: controller.signal });
      clearTimeout(timeoutId);

      if (!res.ok) return;
      const data = await res.json();
      if (!data || !data.found || (!data.monitored && !data.db_info?.applied)) return;

      const host = document.createElement("div");
      host.id = "gcc-radar-badge-host";
      host.style.all = "initial";
      host.style.position = "fixed";
      host.style.bottom = "18px";
      host.style.right = "18px";
      host.style.zIndex = "2147483647";
      host.style.fontFamily = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

      const shadow = host.attachShadow({ mode: "open" });
      const badgeBg = "rgba(11, 19, 41, 0.94)";
      const borderClr = data.db_info?.applied
        ? "rgba(16, 185, 129, 0.45)"
        : (data.db_info?.is_dismissed ? "rgba(244, 63, 94, 0.45)" : "rgba(56, 189, 248, 0.4)");

      shadow.innerHTML = `
        <style>
          .radar-pill {
            display: flex;
            align-items: center;
            gap: 8px;
            background: ${badgeBg};
            border: 1px solid ${borderClr};
            border-radius: 9999px;
            padding: 6px 12px 6px 10px;
            box-shadow: 0 8px 24px -2px rgba(0, 0, 0, 0.65), 0 0 12px rgba(56, 189, 248, 0.2);
            color: #f8fafc;
            font-size: 11.5px;
            font-weight: 600;
            cursor: pointer;
            backdrop-filter: blur(12px);
            user-select: none;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
          }
          .radar-pill:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 28px -2px rgba(0, 0, 0, 0.75), 0 0 16px rgba(56, 189, 248, 0.35);
          }
          .radar-icon {
            font-size: 13px;
          }
          .radar-text {
            color: #e2e8f0;
          }
          .radar-direct-btn {
            background: linear-gradient(135deg, #0284c7, #4f46e5);
            color: #ffffff;
            border: none;
            border-radius: 9999px;
            padding: 2.5px 8px;
            font-size: 10.5px;
            font-weight: 700;
            text-decoration: none;
            margin-left: 2px;
            display: inline-flex;
            align-items: center;
            gap: 3px;
            transition: opacity 0.15s ease;
          }
          .radar-direct-btn:hover {
            opacity: 0.9;
          }
          .close-btn {
            background: none;
            border: none;
            color: #94a3b8;
            font-size: 13px;
            line-height: 1;
            padding: 0 0 0 4px;
            cursor: pointer;
          }
          .close-btn:hover {
            color: #f8fafc;
          }
        </style>
        <div class="radar-pill" title="Click to open GCC Job Radar Side Panel">
          <span class="radar-icon">🎯</span>
          <span class="radar-text">${data.badge_label}</span>
          ${data.direct_ats_url ? `<a class="radar-direct-btn" href="${data.direct_ats_url}" target="_blank" rel="noopener noreferrer">Direct ATS ↗</a>` : ""}
          <button class="close-btn" title="Dismiss badge">×</button>
        </div>
      `;

      shadow.querySelector(".radar-pill").addEventListener("click", (e) => {
        if (e.target.closest(".radar-direct-btn") || e.target.closest(".close-btn")) return;
        chrome.runtime.sendMessage({ action: "open_side_panel" });
      });

      shadow.querySelector(".close-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        host.remove();
      });

      document.body.appendChild(host);
    } catch (e) {
      // Silently ignore if bridge is offline or aborted
    }
  }, 1200);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initFloatingRadarBadge);
} else {
  initFloatingRadarBadge();
}
