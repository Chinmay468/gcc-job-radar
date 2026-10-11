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

/**
 * --------------------------------------------------------------------------
 * 1-Click ATS Form Autofill Engine (Phase 4)
 * Supports Greenhouse, Lever, Ashby, Workday, SmartRecruiters, and custom ATS.
 * --------------------------------------------------------------------------
 */

function getElementContextText(el) {
  const parts = [];
  if (el.id) parts.push(el.id);
  if (el.name) parts.push(el.name);
  if (el.placeholder) parts.push(el.placeholder);
  if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
  if (el.getAttribute("autocomplete")) parts.push(el.getAttribute("autocomplete"));
  if (el.getAttribute("data-automation-id")) parts.push(el.getAttribute("data-automation-id"));
  if (el.getAttribute("data-qa")) parts.push(el.getAttribute("data-qa"));

  // Check explicit label
  if (el.id) {
    try {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) parts.push(label.innerText);
    } catch (e) {}
  }

  // Check parent label or form-group container
  const parentContainer = el.closest("label, .form-group, .field, [class*='field'], [class*='question'], [class*='input-wrapper']");
  if (parentContainer) {
    const clone = parentContainer.cloneNode(true);
    clone.querySelectorAll("input, select, textarea, button").forEach((c) => c.remove());
    parts.push(clone.innerText);
  }

  // Preceding sibling or heading
  const prev = el.previousElementSibling;
  if (prev && (prev.tagName === "LABEL" || prev.tagName === "SPAN" || prev.tagName === "DIV")) {
    parts.push(prev.innerText);
  }

  return parts.join(" ").toLowerCase().replace(/\s+/g, " ");
}

function highlightFilledElement(el) {
  const origOutline = el.style.outline;
  const origBg = el.style.backgroundColor;
  el.style.outline = "2px solid #10b981";
  el.style.backgroundColor = "rgba(16, 185, 129, 0.08)";
  el.style.transition = "all 0.3s ease";
  setTimeout(() => {
    el.style.outline = origOutline;
    el.style.backgroundColor = origBg;
  }, 2500);
}

function setNativeInputValue(el, value) {
  if (!el || value === undefined || value === null) return false;
  try {
    el.focus();
    const tag = el.tagName.toLowerCase();
    const nativeSetter = Object.getOwnPropertyDescriptor(
      tag === "textarea" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype,
      "value"
    )?.set;

    if (nativeSetter) {
      nativeSetter.call(el, value);
    } else {
      el.value = value;
    }

    el.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true, cancelable: true }));

    highlightFilledElement(el);
    return true;
  } catch (e) {
    el.value = value;
    highlightFilledElement(el);
    return true;
  }
}

function setNativeSelectValue(el, matchText) {
  if (!el || !el.options || !matchText) return false;
  const target = matchText.toLowerCase().trim();
  for (let i = 0; i < el.options.length; i++) {
    const opt = el.options[i];
    const text = opt.text.toLowerCase().trim();
    const val = (opt.value || "").toLowerCase().trim();
    if (text === target || val === target || text.includes(target) || val.includes(target)) {
      el.selectedIndex = i;
      el.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
      highlightFilledElement(el);
      return true;
    }
  }
  return false;
}

function setNativeCheckboxValue(el, targetChecked) {
  if (!el) return false;
  try {
    el.focus();
    const shouldCheck = Boolean(targetChecked);
    if (el.checked !== shouldCheck) {
      try {
        el.click();
      } catch (e) {}
      if (el.checked !== shouldCheck) {
        el.checked = shouldCheck;
      }
    }
    el.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true, cancelable: true }));
    highlightFilledElement(el);
    return true;
  } catch (e) {
    el.checked = Boolean(targetChecked);
    highlightFilledElement(el);
    return true;
  }
}

function parseYearMonth(dateStr) {
  if (!dateStr) return null;
  const match = String(dateStr).trim().match(/^(\d{4})[-/.](\d{1,2})/);
  if (!match) return null;
  const year = match[1];
  const monthNum = parseInt(match[2], 10);
  if (isNaN(monthNum) || monthNum < 1 || monthNum > 12) return null;
  const monthNames = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  const monthName = monthNames[monthNum - 1];
  const monthShort = monthName.slice(0, 3);
  const monthPad = String(monthNum).padStart(2, "0");
  return {
    year,
    monthNum,
    monthPad,
    monthName,
    monthShort,
  };
}

function fillEmploymentDate(el, dateStr, ctx) {
  if (!el || !dateStr) return false;
  const parsed = parseYearMonth(dateStr);
  if (!parsed) return false;

  const tag = el.tagName.toLowerCase();
  const context = (ctx || "").toLowerCase();

  // Handle <select> elements (e.g. Workday, Greenhouse month/year pickers)
  if (tag === "select") {
    // 1. Explicit month context
    if (/(month)/i.test(context) && !/(year)/i.test(context)) {
      return (
        setNativeSelectValue(el, parsed.monthName) ||
        setNativeSelectValue(el, parsed.monthPad) ||
        setNativeSelectValue(el, String(parsed.monthNum)) ||
        setNativeSelectValue(el, parsed.monthShort)
      );
    }
    // 2. Explicit year context
    if (/(year)/i.test(context) && !/(month)/i.test(context)) {
      return setNativeSelectValue(el, parsed.year);
    }
    // 3. Infer from select options
    const optTexts = Array.from(el.options).map((o) => (o.text || "").toLowerCase());
    const optVals = Array.from(el.options).map((o) => (o.value || "").toLowerCase());
    const isMonthSelect =
      optTexts.some((t) => t.includes("january") || t.includes("may") || t.includes("jan")) ||
      optVals.some((v) => v === "01" || v === "1" || v === "may");
    if (isMonthSelect) {
      return (
        setNativeSelectValue(el, parsed.monthName) ||
        setNativeSelectValue(el, parsed.monthPad) ||
        setNativeSelectValue(el, String(parsed.monthNum)) ||
        setNativeSelectValue(el, parsed.monthShort)
      );
    }
    const isYearSelect =
      optTexts.some((t) => /20\d\d/.test(t)) ||
      optVals.some((v) => /20\d\d/.test(v));
    if (isYearSelect) {
      return setNativeSelectValue(el, parsed.year);
    }
    // Fallback: try month name, then year, then month number
    return (
      setNativeSelectValue(el, parsed.monthName) ||
      setNativeSelectValue(el, parsed.year) ||
      setNativeSelectValue(el, parsed.monthPad)
    );
  }

  // Handle standard <input> types
  const type = (el.type || "").toLowerCase();
  if (type === "month") {
    return setNativeInputValue(el, `${parsed.year}-${parsed.monthPad}`);
  }
  if (type === "date") {
    return setNativeInputValue(el, `${parsed.year}-${parsed.monthPad}-01`);
  }

  // Handle text inputs
  const placeholder = (el.placeholder || "").toLowerCase();
  if (/(month)/i.test(context) && !/(year)/i.test(context)) {
    return setNativeInputValue(
      el,
      /(mmm|name)/i.test(placeholder + " " + context) ? parsed.monthName : parsed.monthPad
    );
  }
  if (/(year)/i.test(context) && !/(month)/i.test(context)) {
    return setNativeInputValue(el, parsed.year);
  }

  // Combined date input
  if (/(yyyy[-/]mm)/i.test(placeholder + " " + context)) {
    return setNativeInputValue(el, `${parsed.year}-${parsed.monthPad}`);
  }
  if (/(mmm.*yyyy|month.*year)/i.test(placeholder + " " + context)) {
    return setNativeInputValue(el, `${parsed.monthName} ${parsed.year}`);
  }

  // Default MM/YYYY format (e.g. "05/2026")
  return setNativeInputValue(el, `${parsed.monthPad}/${parsed.year}`);
}

function isWorkExperienceContext(el) {
  const ctx = getElementContextText(el);

  // Hard exclusions: Education, availability, notice period, personal info
  if (/(school|university|degree|graduat|college|education|academic|study|major)/i.test(ctx)) {
    return false;
  }
  if (/(notice|available|availability|birth|dob|military|license|certif)/i.test(ctx)) {
    return false;
  }

  // Direct positive context on element itself
  if (/(work|employ|job|position|company|experience|internship|role)/i.test(ctx)) {
    return true;
  }

  // Search enclosing section, fieldset, or form (avoiding single-field wrappers like .form-group)
  const container =
    el.closest(
      "fieldset, [class*='experience'], [class*='work'], [class*='employment'], [class*='job'], " +
      "[data-automation-id*='experience'], [data-automation-id*='work'], [id*='experience'], [id*='work'], " +
      "[class*='form-section'], [class*='section'], [class*='card'], [class*='panel'], [class*='repeater'], " +
      "[class*='container'], form"
    ) || el.parentElement?.parentElement?.parentElement;

  if (container) {
    const containerText = (container.innerText || "").toLowerCase();
    if (
      /(education|academic|degrees|schools)/i.test(containerText) &&
      !/(work|employ|job|position|experience|internship)/i.test(containerText)
    ) {
      return false;
    }
    if (
      /(work.*experience|employment|employer|job.*history|past.*experience|work.*history|my.*experience)/i.test(
        containerText
      )
    ) {
      return true;
    }
    // Check if nearby sibling fields represent job title or company
    const hasJobFields = Array.from(container.querySelectorAll("input, select, textarea")).some((sibling) => {
      if (sibling === el) return false;
      const sibCtx = getElementContextText(sibling);
      return (
        /(job.*title|position.*title|role.*title|^title$|company|employer|organization.*name)/i.test(sibCtx) &&
        !/(school|university|degree|education|study)/i.test(sibCtx)
      );
    });
    if (hasJobFields) return true;
  }

  return false;
}

function showInPageToast(message) {
  const existing = document.getElementById("gcc-autofill-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "gcc-autofill-toast";
  toast.innerText = message;
  Object.assign(toast.style, {
    position: "fixed",
    top: "20px",
    right: "20px",
    background: "linear-gradient(135deg, #064e3b, #047857)",
    color: "#ffffff",
    padding: "10px 18px",
    borderRadius: "8px",
    fontSize: "13px",
    fontWeight: "600",
    boxShadow: "0 8px 24px rgba(0,0,0,0.4), 0 0 12px rgba(16,185,129,0.4)",
    zIndex: "2147483647",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    transition: "opacity 0.3s ease, transform 0.3s ease",
    opacity: "0",
    transform: "translateY(-10px)",
  });

  document.body.appendChild(toast);
  requestAnimationFrame(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";
  });

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-10px)";
    setTimeout(() => toast.remove(), 400);
  }, 3500);
}

function detectAndFillJobForm(customProfile) {
  const defaultWorkHistory = [
    {
      job_title: "Full Stack Developer Intern",
      company: "Yinolite Solution",
      from: "2026-05",
      to: "2026-06",
      is_current: false,
    },
  ];

  const profile = {
    first_name: "Chinmay",
    last_name: "Maheshwari",
    full_name: "Chinmay Maheshwari",
    email: "chinmaymaheshwari.it27@gmail.com",
    phone: "+91 9460449962",
    linkedin: "https://www.linkedin.com/in/chinmay8064/",
    github: "https://github.com/Chinmay468",
    portfolio: "https://github.com/Chinmay468",
    location: "Jaipur, India",
    city: "Jaipur",
    state: "Rajasthan",
    country: "India",
    school: "Jaipur Engineering College and Research Centre",
    university: "Jaipur Engineering College and Research Centre",
    degree: "B.Tech in Information Technology",
    graduation_year: "2027",
    gpa: "8.8",
    notice_period: "Immediate",
    authorized_in_country: "Yes",
    visa_sponsorship_needed: "No",
    willing_to_relocate: "Yes",
    gender: "Male",
    work_history: defaultWorkHistory,
    ...(customProfile || {}),
  };

  const workHistory =
    profile.work_history && Array.isArray(profile.work_history) && profile.work_history.length > 0
      ? profile.work_history
      : defaultWorkHistory;

  const filledFields = [];
  const openQuestions = [];

  // Track work-history entry indices per container / field group
  const assignedClusterMap = new Map();
  let currentClusterIndex = 0;
  const currentClusterSeenFields = new Set();

  function getWorkHistoryEntry(el, fieldType) {
    // 1. Try to find a distinct entry-level container (e.g. fieldset or repeating experience item)
    const itemContainer = el.closest(
      "[data-automation-id*='experience-item'], [class*='experience-item'], [class*='experienceItem'], " +
      "[class*='work-item'], [class*='employment-item'], [class*='repeater-item'], [class*='item-row'], " +
      "[class*='entry-row'], [class*='entry'], [data-automation-id*='workHistory-item'], fieldset"
    );
    if (itemContainer) {
      if (!assignedClusterMap.has(itemContainer)) {
        assignedClusterMap.set(itemContainer, currentClusterIndex++);
      }
      const idx = assignedClusterMap.get(itemContainer);
      return { entry: workHistory[idx], index: idx };
    }

    // 2. Sequential fallback if no explicit item-level container:
    // If the same field type is encountered again in this cluster, advance to next role!
    if (currentClusterSeenFields.has(fieldType)) {
      currentClusterIndex++;
      currentClusterSeenFields.clear();
    }
    currentClusterSeenFields.add(fieldType);
    const idx = currentClusterIndex;
    return { entry: workHistory[idx], index: idx };
  }

  const inputs = Array.from(document.querySelectorAll("input, textarea, select"));

  inputs.forEach((el) => {
    const type = (el.type || "").toLowerCase();
    if (type === "hidden" || type === "submit" || type === "button") return;

    const ctx = getElementContextText(el);
    const currentVal = (el.value || "").trim();

    // Checkbox handling: specifically handle "I currently work here", skip all other checkboxes
    if (type === "checkbox") {
      if (
        /(currently.*work.*here|current.*employer|i.*currently.*work|currently.*employed|current.*job)/i.test(ctx) ||
        (/(^current$|present)/i.test(ctx) && isWorkExperienceContext(el))
      ) {
        const { entry } = getWorkHistoryEntry(el, "is_current");
        if (entry) {
          if (setNativeCheckboxValue(el, entry.is_current)) {
            filledFields.push("Current Employer");
          }
        }
      }
      return;
    }

    // 1. First Name
    if (/(first.*name|fname|given.*name)/i.test(ctx) && !/(last|full|legal.*name)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.first_name)) filledFields.push("First Name");
      return;
    }

    // 2. Last Name
    if (/(last.*name|lname|surname|family.*name)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.last_name)) filledFields.push("Last Name");
      return;
    }

    // 3. Full Name
    if (/(full.*name|^name$|candidate.*name|legal.*name)/i.test(ctx) && !/(first|last|company|school|user|file)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.full_name)) filledFields.push("Full Name");
      return;
    }

    // 4. Email
    if (type === "email" || /(email|e-mail)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.email)) filledFields.push("Email");
      return;
    }

    // 5. Phone
    if (type === "tel" || /(phone|mobile|cell|contact.*number)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.phone)) filledFields.push("Phone");
      return;
    }

    // 6. LinkedIn
    if (/(linkedin|linked.*in)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.linkedin)) filledFields.push("LinkedIn");
      return;
    }

    // 7. GitHub
    if (/(github|git.*hub)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.github)) filledFields.push("GitHub");
      return;
    }

    // 8. Portfolio / Website
    if (/(portfolio|personal.*site|website|webpage|other.*url)/i.test(ctx) && !/(linkedin|github)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.portfolio || profile.github)) filledFields.push("Portfolio");
      return;
    }

    // 9. Location / City
    if (/(current.*location|city|address|residence)/i.test(ctx) && !/(relocat|work.*auth)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.city || profile.location)) filledFields.push("Location");
      return;
    }

    // 10. University / School
    if (/(university|college|school|institution|education)/i.test(ctx) && !/(degree|grad)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, "Jaipur")) filledFields.push("University");
      } else {
        if (setNativeInputValue(el, profile.university || profile.school)) filledFields.push("University");
      }
      return;
    }

    // 11. Degree / Major
    if (/(degree|field.*of.*study|discipline|major)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, "Bachelor")) filledFields.push("Degree");
      } else {
        if (setNativeInputValue(el, profile.degree)) filledFields.push("Degree");
      }
      return;
    }

    // 12. Graduation Year
    if (/(grad.*year|graduation.*year|completion.*year)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, profile.graduation_year)) filledFields.push("Grad Year");
      } else {
        if (setNativeInputValue(el, profile.graduation_year)) filledFields.push("Grad Year");
      }
      return;
    }

    // 13. GPA / Percentage
    if (/(gpa|cgpa|percentage)/i.test(ctx)) {
      if (setNativeInputValue(el, profile.gpa)) filledFields.push("GPA");
      return;
    }

    // 14. Notice Period
    if (/(notice.*period|how.*soon|available.*to.*start|earliest.*start)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, "Immediate")) filledFields.push("Notice Period");
      } else {
        if (setNativeInputValue(el, profile.notice_period)) filledFields.push("Notice Period");
      }
      return;
    }

    // 15. Work Authorization (Yes)
    if (/(authorized.*to.*work|work.*authorization|legally.*authorized|eligible.*to.*work)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, "Yes")) filledFields.push("Work Authorization");
      } else if (type === "text") {
        if (setNativeInputValue(el, "Yes")) filledFields.push("Work Authorization");
      }
      return;
    }

    // 16. Visa Sponsorship (No)
    if (/(require.*sponsorship|visa.*sponsorship|sponsorship.*now.*or.*in.*the.*future)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, "No")) filledFields.push("Visa Sponsorship");
      } else if (type === "text") {
        if (setNativeInputValue(el, "No")) filledFields.push("Visa Sponsorship");
      }
      return;
    }

    // 17. Relocation (Yes)
    if (/(willing.*to.*relocate|open.*to.*relocat|relocation)/i.test(ctx)) {
      if (el.tagName.toLowerCase() === "select") {
        if (setNativeSelectValue(el, "Yes")) filledFields.push("Relocation");
      } else if (type === "text") {
        if (setNativeInputValue(el, "Yes")) filledFields.push("Relocation");
      }
      return;
    }

    // 18. Job Title
    if (
      /(job.*title|position.*title|role.*title|^title$|position.*held)/i.test(ctx) &&
      el.tagName.toLowerCase() !== "textarea" &&
      !/(school|degree|university|institution|mr|mrs|ms|salutation|prefix|why|describe)/i.test(ctx)
    ) {
      const { entry } = getWorkHistoryEntry(el, "title");
      if (entry && entry.job_title) {
        if (el.tagName.toLowerCase() === "select") {
          if (setNativeSelectValue(el, entry.job_title)) filledFields.push("Job Title");
        } else {
          if (setNativeInputValue(el, entry.job_title)) filledFields.push("Job Title");
        }
      }
      return;
    }

    // 19. Company / Employer
    if (
      /(company.*name|employer|organization.*name|^company$|^organization$|\bcompany\b|\bemployer\b)/i.test(ctx) &&
      el.tagName.toLowerCase() !== "textarea" &&
      !/(school|university|college|institution|degree|url|website|site|domain|why|describe)/i.test(ctx)
    ) {
      const { entry } = getWorkHistoryEntry(el, "company");
      if (entry && entry.company) {
        if (el.tagName.toLowerCase() === "select") {
          if (setNativeSelectValue(el, entry.company)) filledFields.push("Company");
        } else {
          if (setNativeInputValue(el, entry.company)) filledFields.push("Company");
        }
      }
      return;
    }

    // 20. Employment Start Date ("From")
    if (
      /(^from$|\bfrom\b.*date|start.*date|employment.*start|from.*date|date.*from|start.*month|start.*year|from.*month|from.*year)/i.test(ctx) &&
      el.tagName.toLowerCase() !== "textarea" &&
      isWorkExperienceContext(el)
    ) {
      const subType = /(month)/i.test(ctx) ? "from_month" : (/(year)/i.test(ctx) ? "from_year" : "from");
      const { entry } = getWorkHistoryEntry(el, subType);
      if (entry && entry.from) {
        if (fillEmploymentDate(el, entry.from, ctx)) filledFields.push("Employment Dates");
      }
      return;
    }

    // 21. Employment End Date ("To")
    if (
      /(^to$|\bto\b.*date|end.*date|employment.*end|to.*date|date.*to|end.*month|end.*year|to.*month|to.*year)/i.test(ctx) &&
      el.tagName.toLowerCase() !== "textarea" &&
      isWorkExperienceContext(el)
    ) {
      const subType = /(month)/i.test(ctx) ? "to_month" : (/(year)/i.test(ctx) ? "to_year" : "to");
      const { entry } = getWorkHistoryEntry(el, subType);
      if (entry) {
        if (entry.is_current) {
          if (el.tagName.toLowerCase() === "input" && (type === "text" || !type)) {
            if (setNativeInputValue(el, "Present")) filledFields.push("Employment Dates");
          }
        } else if (entry.to) {
          if (fillEmploymentDate(el, entry.to, ctx)) filledFields.push("Employment Dates");
        }
      }
      return;
    }

    // 22. Open screening questions (e.g. textarea or text input with questions)
    if (el.tagName.toLowerCase() === "textarea" || (type === "text" && ctx.length > 25)) {
      if (!currentVal && ctx.length > 15) {
        openQuestions.push({
          id: el.id || `field_${openQuestions.length}`,
          context: ctx.slice(0, 100),
          tag: el.tagName.toLowerCase(),
        });
      }
    }
  });

  const uniqueFilled = Array.from(new Set(filledFields));
  const count = uniqueFilled.length;

  if (count > 0) {
    showInPageToast(`Radar Autofill: ${count} fields filled (${uniqueFilled.slice(0, 3).join(", ")}${count > 3 ? "..." : ""})`);
  } else {
    showInPageToast(`Radar Autofill: No matching empty fields found.`);
  }

  return {
    filled_count: count,
    filled_fields: uniqueFilled,
    open_questions: openQuestions,
  };
}

function fillSpecificField(fieldId, value) {
  let target = null;
  if (fieldId) target = document.getElementById(fieldId);
  if (!target) {
    target = document.querySelector("textarea:focus, input:focus, textarea");
  }
  if (!target) return { filled: false, error: "No target input field found." };

  const ok = setNativeInputValue(target, value);
  if (ok) {
    showInPageToast("Answer inserted into form field");
  }
  return { filled: ok };
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
  } else if (request.action === "autofill_form") {
    try {
      const res = detectAndFillJobForm(request.profile);
      sendResponse({ success: true, ...res });
    } catch (err) {
      sendResponse({ success: false, error: err.message });
    }
  } else if (request.action === "fill_specific_field") {
    try {
      const res = fillSpecificField(request.fieldId, request.value);
      sendResponse({ success: true, ...res });
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
          .radar-autofill-btn {
            background: linear-gradient(135deg, #059669, #10b981);
            color: #ffffff;
            border: none;
            border-radius: 9999px;
            padding: 2.5px 8px;
            font-size: 10.5px;
            font-weight: 700;
            cursor: pointer;
            margin-left: 2px;
            display: inline-flex;
            align-items: center;
            gap: 3px;
            transition: opacity 0.15s ease, transform 0.15s ease;
          }
          .radar-autofill-btn:hover {
            opacity: 0.9;
            transform: scale(1.04);
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
          ${document.querySelectorAll("input:not([type='hidden']), textarea").length >= 2 ? `<button class="radar-autofill-btn" title="1-Click ATS Form Autofill">⚡ Autofill</button>` : ""}
          ${data.direct_ats_url ? `<a class="radar-direct-btn" href="${data.direct_ats_url}" target="_blank" rel="noopener noreferrer">Direct ATS ↗</a>` : ""}
          <button class="close-btn" title="Dismiss badge">×</button>
        </div>
      `;

      const autofillBtn = shadow.querySelector(".radar-autofill-btn");
      if (autofillBtn) {
        autofillBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          detectAndFillJobForm();
        });
      }

      shadow.querySelector(".radar-pill").addEventListener("click", (e) => {
        if (e.target.closest(".radar-direct-btn") || e.target.closest(".radar-autofill-btn") || e.target.closest(".close-btn")) return;
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
