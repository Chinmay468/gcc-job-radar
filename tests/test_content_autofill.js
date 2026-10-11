/**
 * test_content_autofill.js
 * Comprehensive unit test suite for chrome-extension/content.js ATS Form Autofill engine,
 * verifying employment history fields (Job Title, Company, Dates, Current Employer checkbox),
 * multiple experience clusters, Education section isolation, and non-regression of existing 17 rules.
 */

const fs = require("fs");
const path = require("path");
const assert = require("assert");

// Read content.js
const contentJsPath = path.resolve(__dirname, "../chrome-extension/content.js");
const contentJsCode = fs.readFileSync(contentJsPath, "utf8");

// Mock browser environment
class MockNode {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.type = attrs.type || (tag === "input" ? "text" : "");
    this.id = attrs.id || "";
    this.name = attrs.name || "";
    this.placeholder = attrs.placeholder || "";
    this.value = attrs.value || "";
    this.checked = attrs.checked !== undefined ? attrs.checked : false;
    this.options = attrs.options || [];
    this.selectedIndex = attrs.selectedIndex || 0;
    if (attrs.innerText !== undefined) {
      this._explicitText = attrs.innerText;
    }
    this.className = attrs.className || "";
    this.attributes = { ...attrs };
    this.style = {};
    this.parentElement = null;
    this.children = [];
    this._dispatchedEvents = [];
  }

  get previousElementSibling() {
    if (!this.parentElement) return null;
    const idx = this.parentElement.children.indexOf(this);
    return idx > 0 ? this.parentElement.children[idx - 1] : null;
  }

  get innerText() {
    if (this._explicitText !== undefined && this.children.length === 0) {
      return this._explicitText;
    }
    if (this.children.length > 0) {
      const parts = [];
      if (this._explicitText) parts.push(this._explicitText);
      for (const c of this.children) {
        const t = c.innerText;
        if (t) parts.push(t);
      }
      return parts.join(" ");
    }
    return this._explicitText || "";
  }

  set innerText(val) {
    this._explicitText = val;
  }

  getAttribute(name) {
    return this.attributes[name] || null;
  }

  setAttribute(name, val) {
    this.attributes[name] = val;
  }

  focus() {}

  dispatchEvent(ev) {
    this._dispatchedEvents.push(ev.type);
    return true;
  }

  click() {
    this.checked = !this.checked;
    this.dispatchEvent({ type: "click" });
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  cloneNode(deep) {
    const copy = new MockNode(this.tagName.toLowerCase(), {
      ...this.attributes,
      type: this.type,
      id: this.id,
      name: this.name,
      placeholder: this.placeholder,
      value: this.value,
      checked: this.checked,
      className: this.className,
    });
    if (this._explicitText !== undefined) copy.innerText = this._explicitText;
    if (deep) {
      for (const c of this.children) {
        copy.appendChild(c.cloneNode(true));
      }
    }
    return copy;
  }

  remove() {
    if (this.parentElement) {
      const idx = this.parentElement.children.indexOf(this);
      if (idx !== -1) this.parentElement.children.splice(idx, 1);
    }
  }

  closest(selector) {
    let curr = this.parentElement;
    while (curr) {
      if (curr.matches(selector)) return curr;
      curr = curr.parentElement;
    }
    return null;
  }

  matches(selector) {
    if (!selector) return false;
    const parts = selector.split(",").map((s) => s.trim());
    for (const part of parts) {
      if (part.toLowerCase() === this.tagName.toLowerCase()) return true;
      if (part === "fieldset" && this.tagName === "FIELDSET") return true;
      if (part === "form" && this.tagName === "FORM") return true;
      if (part.startsWith(".")) {
        const cls = part.slice(1);
        if (this.className.split(/\s+/).includes(cls)) return true;
      }
      if (part.includes("[class*='")) {
        const match = part.match(/\[class\*='(.*?)'\]/);
        if (match && this.className.includes(match[1])) return true;
      }
      if (part.includes("[data-automation-id*='")) {
        const match = part.match(/\[data-automation-id\*='(.*?)'\]/);
        const autoId = this.getAttribute("data-automation-id") || "";
        if (match && autoId.includes(match[1])) return true;
      }
      if (part.includes("[id*='")) {
        const match = part.match(/\[id\*='(.*?)'\]/);
        if (match && this.id.includes(match[1])) return true;
      }
    }
    return false;
  }

  querySelectorAll(selector) {
    const res = [];
    function traverse(node) {
      for (const child of node.children) {
        if (child.matches(selector)) res.push(child);
        traverse(child);
      }
    }
    traverse(this);
    return res;
  }
}

// Global environment setup for evaluating content.js functions
const rootDoc = new MockNode("div");
global.document = {
  getElementById: (id) => {
    function find(node) {
      if (node.id === id) return node;
      for (const c of node.children) {
        const f = find(c);
        if (f) return f;
      }
      return null;
    }
    return find(rootDoc);
  },
  querySelector: (sel) => {
    const list = rootDoc.querySelectorAll(sel);
    return list.length > 0 ? list[0] : null;
  },
  querySelectorAll: (sel) => rootDoc.querySelectorAll(sel),
  createElement: (tag) => new MockNode(tag),
  body: rootDoc,
};
global.window = {
  location: { href: "https://example.com" },
  HTMLInputElement: { prototype: {} },
  HTMLTextAreaElement: { prototype: {} },
};
global.Event = class Event {
  constructor(type, opts) {
    this.type = type;
    this.bubbles = opts?.bubbles || false;
    this.cancelable = opts?.cancelable || false;
  }
};
global.CSS = { escape: (s) => s };
global.requestAnimationFrame = (fn) => fn();
global.chrome = {
  runtime: { onMessage: { addListener: () => {} } },
};

// Evaluate content.js functions into this context
eval(contentJsCode);

function resetDOM() {
  rootDoc.children = [];
}

console.log("--- Starting ATS Autofill Tests ---");

// Test 1: Cisco Apply Form (careers.cisco.com)
// Work experience section: Job Title, Company, "I currently work here" checkbox, From, To
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form", { className: "cisco-job-application" }));
  const expSection = form.appendChild(
    new MockNode("div", { className: "form-section experience-section", innerText: "My Experience - Work experience" })
  );

  const titleGroup = expSection.appendChild(new MockNode("div", { className: "form-group" }));
  titleGroup.appendChild(new MockNode("label", { innerText: "Job Title *" }));
  const titleInput = titleGroup.appendChild(new MockNode("input", { name: "jobTitle", placeholder: "Job Title" }));

  const companyGroup = expSection.appendChild(new MockNode("div", { className: "form-group" }));
  companyGroup.appendChild(new MockNode("label", { innerText: "Company *" }));
  const companyInput = companyGroup.appendChild(new MockNode("input", { name: "company", placeholder: "Company" }));

  const checkGroup = expSection.appendChild(new MockNode("div", { className: "form-group checkbox-wrapper" }));
  const currentCheckbox = checkGroup.appendChild(
    new MockNode("input", { type: "checkbox", name: "isCurrentJob", checked: false })
  );
  checkGroup.appendChild(new MockNode("label", { innerText: "I currently work here" }));

  const fromGroup = expSection.appendChild(new MockNode("div", { className: "form-group" }));
  fromGroup.appendChild(new MockNode("label", { innerText: "From *" }));
  const fromInput = fromGroup.appendChild(new MockNode("input", { name: "startDate", placeholder: "MM/YYYY" }));

  const toGroup = expSection.appendChild(new MockNode("div", { className: "form-group" }));
  toGroup.appendChild(new MockNode("label", { innerText: "To *" }));
  const toInput = toGroup.appendChild(new MockNode("input", { name: "endDate", placeholder: "MM/YYYY" }));

  const res = detectAndFillJobForm();

  assert.strictEqual(titleInput.value, "Full Stack Developer Intern", "Job title should match Yinolite internship");
  assert.strictEqual(companyInput.value, "Yinolite Solution", "Company should match Yinolite Solution");
  assert.strictEqual(currentCheckbox.checked, false, "Current employer checkbox should be false");
  assert.strictEqual(fromInput.value, "05/2026", "From date should be 05/2026");
  assert.strictEqual(toInput.value, "06/2026", "To date should be 06/2026");

  assert(res.filled_fields.includes("Job Title"), "filled_fields should include Job Title");
  assert(res.filled_fields.includes("Company"), "filled_fields should include Company");
  assert(res.filled_fields.includes("Current Employer"), "filled_fields should include Current Employer");
  assert(res.filled_fields.includes("Employment Dates"), "filled_fields should include Employment Dates");

  console.log("✔ Test 1 Passed: Cisco apply page work experience section filled accurately.");
}

// Test 2: Workday / Greenhouse with month & year <select> dropdowns
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form", { className: "workday-application" }));
  const workBlock = form.appendChild(
    new MockNode("div", {
      className: "experience-item",
      attributes: { "data-automation-id": "experience-item" },
      innerText: "Work Experience",
    })
  );

  const titleInput = workBlock.appendChild(
    new MockNode("input", {
      attributes: { "data-automation-id": "jobTitle", placeholder: "Position Title" },
      name: "title",
    })
  );
  const companyInput = workBlock.appendChild(
    new MockNode("input", {
      attributes: { "data-automation-id": "employer", placeholder: "Employer" },
      name: "company",
    })
  );

  const startMonthSelect = workBlock.appendChild(
    new MockNode("select", {
      attributes: { "data-automation-id": "startMonth", "aria-label": "Start Month" },
      name: "startMonth",
      options: [
        { text: "Select Month", value: "" },
        { text: "January", value: "01" },
        { text: "May", value: "05" },
        { text: "June", value: "06" },
      ],
    })
  );

  const startYearSelect = workBlock.appendChild(
    new MockNode("select", {
      attributes: { "data-automation-id": "startYear", "aria-label": "Start Year" },
      name: "startYear",
      options: [
        { text: "Year", value: "" },
        { text: "2024", value: "2024" },
        { text: "2025", value: "2025" },
        { text: "2026", value: "2026" },
      ],
    })
  );

  const endMonthSelect = workBlock.appendChild(
    new MockNode("select", {
      attributes: { "data-automation-id": "endMonth", "aria-label": "End Month" },
      name: "endMonth",
      options: [
        { text: "Select Month", value: "" },
        { text: "January", value: "01" },
        { text: "May", value: "05" },
        { text: "June", value: "06" },
      ],
    })
  );

  const endYearSelect = workBlock.appendChild(
    new MockNode("select", {
      attributes: { "data-automation-id": "endYear", "aria-label": "End Year" },
      name: "endYear",
      options: [
        { text: "Year", value: "" },
        { text: "2024", value: "2024" },
        { text: "2025", value: "2025" },
        { text: "2026", value: "2026" },
      ],
    })
  );

  const res = detectAndFillJobForm();

  assert.strictEqual(titleInput.value, "Full Stack Developer Intern");
  assert.strictEqual(companyInput.value, "Yinolite Solution");
  assert.strictEqual(startMonthSelect.selectedIndex, 2, "Start month should select 'May' (index 2)");
  assert.strictEqual(startYearSelect.selectedIndex, 3, "Start year should select '2026' (index 3)");
  assert.strictEqual(endMonthSelect.selectedIndex, 3, "End month should select 'June' (index 3)");
  assert.strictEqual(endYearSelect.selectedIndex, 3, "End year should select '2026' (index 3)");

  console.log("✔ Test 2 Passed: Workday/Greenhouse separate month & year selects filled accurately.");
}

// Test 3: Multiple Work Experience blocks (repeated entries)
// Block 1 should fill Yinolite, Block 2 should remain unfilled when profile has 1 entry
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));

  // Block 1
  const block1 = form.appendChild(new MockNode("fieldset", { innerText: "Employment 1" }));
  const t1 = block1.appendChild(new MockNode("input", { name: "job_title", placeholder: "Job Title" }));
  const c1 = block1.appendChild(new MockNode("input", { name: "company", placeholder: "Company" }));
  const d1 = block1.appendChild(new MockNode("input", { name: "start_date", placeholder: "Start Date" }));

  // Block 2
  const block2 = form.appendChild(new MockNode("fieldset", { innerText: "Employment 2" }));
  const t2 = block2.appendChild(new MockNode("input", { name: "job_title", placeholder: "Job Title" }));
  const c2 = block2.appendChild(new MockNode("input", { name: "company", placeholder: "Company" }));
  const d2 = block2.appendChild(new MockNode("input", { name: "start_date", placeholder: "Start Date" }));

  const res = detectAndFillJobForm();

  assert.strictEqual(t1.value, "Full Stack Developer Intern");
  assert.strictEqual(c1.value, "Yinolite Solution");
  assert.strictEqual(d1.value, "05/2026");

  // Block 2 must NOT be filled or duplicated
  assert.strictEqual(t2.value, "", "Second block job title should remain empty");
  assert.strictEqual(c2.value, "", "Second block company should remain empty");
  assert.strictEqual(d2.value, "", "Second block date should remain empty");

  console.log("✔ Test 3 Passed: Multi-block repeating containers leave excess entries unfilled.");
}

// Test 4: Education section with "From" and "To" dates MUST NOT be touched
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));

  // Education Section
  const eduSection = form.appendChild(new MockNode("div", { innerText: "Education History" }));
  const schoolGroup = eduSection.appendChild(new MockNode("div", { className: "form-group" }));
  schoolGroup.appendChild(new MockNode("label", { innerText: "University / School" }));
  const schoolInput = schoolGroup.appendChild(new MockNode("input", { name: "school" }));

  const degreeGroup = eduSection.appendChild(new MockNode("div", { className: "form-group" }));
  degreeGroup.appendChild(new MockNode("label", { innerText: "Degree" }));
  const degreeInput = degreeGroup.appendChild(new MockNode("input", { name: "degree" }));

  const eduFromGroup = eduSection.appendChild(new MockNode("div", { className: "form-group" }));
  eduFromGroup.appendChild(new MockNode("label", { innerText: "From" }));
  const eduFromInput = eduFromGroup.appendChild(new MockNode("input", { name: "eduFrom" }));

  const eduToGroup = eduSection.appendChild(new MockNode("div", { className: "form-group" }));
  eduToGroup.appendChild(new MockNode("label", { innerText: "To" }));
  const eduToInput = eduToGroup.appendChild(new MockNode("input", { name: "eduTo" }));

  const res = detectAndFillJobForm();

  assert.strictEqual(schoolInput.value, "Jaipur Engineering College and Research Centre");
  assert.strictEqual(degreeInput.value, "B.Tech in Information Technology");
  // Education date ranges must NOT be filled with 05/2026 employment dates
  assert.strictEqual(eduFromInput.value, "", "Education From date must remain untouched by work history");
  assert.strictEqual(eduToInput.value, "", "Education To date must remain untouched by work history");

  console.log("✔ Test 4 Passed: Education From/To date ranges properly isolated and untouched.");
}

// Test 5: Existing 17 field matchers regression check
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));

  const fn = form.appendChild(new MockNode("input", { name: "first_name", placeholder: "First Name" }));
  const ln = form.appendChild(new MockNode("input", { name: "last_name", placeholder: "Last Name" }));
  const em = form.appendChild(new MockNode("input", { type: "email", name: "email" }));
  const ph = form.appendChild(new MockNode("input", { type: "tel", name: "phone" }));
  const li = form.appendChild(new MockNode("input", { name: "linkedin", placeholder: "LinkedIn Profile" }));
  const gh = form.appendChild(new MockNode("input", { name: "github", placeholder: "GitHub URL" }));
  const pf = form.appendChild(new MockNode("input", { name: "portfolio", placeholder: "Personal Website" }));
  const loc = form.appendChild(new MockNode("input", { name: "city", placeholder: "Current City" }));
  const gpa = form.appendChild(new MockNode("input", { name: "gpa", placeholder: "CGPA" }));
  const notice = form.appendChild(new MockNode("input", { name: "notice_period", placeholder: "Notice Period" }));
  const auth = form.appendChild(new MockNode("input", { name: "authorized", placeholder: "Authorized to work in India?" }));
  const visa = form.appendChild(new MockNode("input", { name: "visa", placeholder: "Require Visa Sponsorship?" }));
  const rel = form.appendChild(new MockNode("input", { name: "relocation", placeholder: "Willing to relocate?" }));

  const res = detectAndFillJobForm();

  assert.strictEqual(fn.value, "Chinmay");
  assert.strictEqual(ln.value, "Maheshwari");
  assert.strictEqual(em.value, "chinmaymaheshwari.it27@gmail.com");
  assert.strictEqual(ph.value, "+91 9460449962");
  assert.strictEqual(li.value, "https://www.linkedin.com/in/chinmay8064/");
  assert.strictEqual(gh.value, "https://github.com/Chinmay468");
  assert.strictEqual(pf.value, "https://github.com/Chinmay468");
  assert.strictEqual(loc.value, "Jaipur");
  assert.strictEqual(gpa.value, "8.8");
  assert.strictEqual(notice.value, "Immediate");
  assert.strictEqual(auth.value, "Yes");
  assert.strictEqual(visa.value, "No");
  assert.strictEqual(rel.value, "Yes");

  assert.strictEqual(res.filled_count, 13);
  console.log("✔ Test 5 Passed: All existing profile matchers working without regression.");
}

// Test 6: Open screening questions (e.g. textarea "Why this company?") not hijacked by Company matcher
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));

  const whyTextarea = form.appendChild(
    new MockNode("textarea", {
      id: "why_join_us",
      placeholder: "Why do you want to work at Cisco?",
      innerText: "",
    })
  );

  const res = detectAndFillJobForm();

  assert.strictEqual(whyTextarea.value, "", "Screening textarea should not be populated as Company");
  assert.strictEqual(res.open_questions.length, 1, "Should record 1 open screening question");
  assert.strictEqual(res.open_questions[0].id, "why_join_us");

  console.log("✔ Test 6 Passed: Screening question textareas not hijacked by Company matcher.");
}

// Test 7: Multi-block repeating containers when candidate profile provides 2 roles
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));

  // Block 1
  const block1 = form.appendChild(new MockNode("fieldset", { innerText: "Employment 1" }));
  const t1 = block1.appendChild(new MockNode("input", { name: "job_title", placeholder: "Job Title" }));
  const c1 = block1.appendChild(new MockNode("input", { name: "company", placeholder: "Company" }));
  const d1 = block1.appendChild(new MockNode("input", { name: "start_date", placeholder: "Start Date" }));

  // Block 2
  const block2 = form.appendChild(new MockNode("fieldset", { innerText: "Employment 2" }));
  const t2 = block2.appendChild(new MockNode("input", { name: "job_title", placeholder: "Job Title" }));
  const c2 = block2.appendChild(new MockNode("input", { name: "company", placeholder: "Company" }));
  const d2 = block2.appendChild(new MockNode("input", { name: "start_date", placeholder: "Start Date" }));

  const customProfile = {
    work_history: [
      {
        job_title: "Full Stack Developer Intern",
        company: "Yinolite Solution",
        from: "2026-05",
        to: "2026-06",
        is_current: false,
      },
      {
        job_title: "Junior Backend Engineer",
        company: "Acme Tech Labs",
        from: "2025-01",
        to: "2025-04",
        is_current: false,
      },
    ],
  };

  const res = detectAndFillJobForm(customProfile);

  assert.strictEqual(t1.value, "Full Stack Developer Intern");
  assert.strictEqual(c1.value, "Yinolite Solution");
  assert.strictEqual(d1.value, "05/2026");

  assert.strictEqual(t2.value, "Junior Backend Engineer");
  assert.strictEqual(c2.value, "Acme Tech Labs");
  assert.strictEqual(d2.value, "01/2025");

  console.log("✔ Test 7 Passed: Multi-role profile fills consecutive experience blocks accurately.");
}

// Test 8: "I currently work here" checkbox with is_current: true
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));
  const expSection = form.appendChild(new MockNode("div", { className: "experience-section" }));
  const currentCheckbox = expSection.appendChild(
    new MockNode("input", { type: "checkbox", name: "isCurrentJob", checked: false })
  );
  expSection.appendChild(new MockNode("label", { innerText: "I currently work here" }));

  const customProfile = {
    work_history: [
      {
        job_title: "Full Stack Developer Intern",
        company: "Yinolite Solution",
        from: "2026-05",
        to: "2026-06",
        is_current: true,
      },
    ],
  };

  detectAndFillJobForm(customProfile);

  assert.strictEqual(currentCheckbox.checked, true, "Checkbox should be checked when is_current is true");
  console.log("✔ Test 8 Passed: Current employer checkbox checked when is_current is true.");
}

// Test 9: Native event dispatching verification
{
  resetDOM();
  const form = rootDoc.appendChild(new MockNode("form"));
  const expSection = form.appendChild(new MockNode("div", { className: "experience-section" }));
  const titleInput = expSection.appendChild(new MockNode("input", { name: "jobTitle", placeholder: "Job Title" }));

  detectAndFillJobForm();

  assert(titleInput._dispatchedEvents.includes("input"), "Must dispatch input event");
  assert(titleInput._dispatchedEvents.includes("change"), "Must dispatch change event");
  assert(titleInput._dispatchedEvents.includes("blur"), "Must dispatch blur event");

  console.log("✔ Test 9 Passed: Native input, change, and blur events dispatched for reactive frameworks.");
}

console.log("\nALL 9 TESTS PASSED SUCCESSFULLY! 🎉");

