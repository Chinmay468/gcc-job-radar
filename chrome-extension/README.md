# 🎯 GCC Job Radar & Resume Tailor — Chrome Extension

A Chrome Extension (Manifest V3) designed to evaluate job descriptions on the fly, score alignment with your core engineering stack, and generate 1-click tailored PDF resumes locally via **Tectonic** — **completely eliminating Overleaf and manual LaTeX copying**.

---

## ⚡ Key Capabilities

1. **Persistent Chrome Side Panel (v2.2)**:
   - Sits docked side-by-side with any job board or ATS application without closing when you click around the page.
   - Live tab synchronization automatically detects when you switch tabs to a new job opening.
2. **Auto-Detects Job Postings Across 10+ Portals**:
   - Native deep extractors for **Ashby (`ashbyhq.com`), LinkedIn, Wellfound, Indeed, Naukri, Greenhouse, Lever, Workday, SmartRecruiters, Instahyre, and Unstop**.
   - Schema.org `application/ld+json` structured parser + universal DOM fallback (`<article>`, `<main>`, or user-selected text).
3. **Instant Fit Evaluation**:
   - Runs fast rule checks against Chinmay's profile (Java, Spring Boot, React, Node.js, SQL, 0–2 YOE).
   - Calls Groq AI to calculate fit score (0–100), verdict (`APPLY`, `BORDERLINE`, `SKIP`), matched vs. missing skills, and green flags / hard blocks.
4. **1-Click Local Resume Tailoring & PDF Compilation (Zero Overleaf)**:
   - Prompts Groq to tailor `master_resume.tex` specifically for the target JD.
   - Compiles `.tex` $\to$ `.pdf` directly using standalone [`tools/bin/tectonic.exe`](file:///D:/Projects/gcc-job-radar/tools/bin/tectonic.exe) in **~1.4 seconds**.
   - Responsive embedded PDF preview with iterative feedback tweaking and instant downloads.
5. **Direct Radar DB Sync**:
   - 1-click **"Mark Applied in Radar"** saves the job into your local `gcc_jobs.db` tracker and prepares it for Turso Cloud sync.

---

## 🚀 Installation & Setup (2 Minutes)

### Step 1: Start the Local Bridge Daemon
The Chrome Extension communicates with your local environment through a localhost daemon on `http://127.0.0.1:8765`:

Double click:
```cmd
start_extension_bridge.bat
```
*(Or run in your terminal: `uv run python tools/extension_bridge.py`)*

You will see:
```text
🚀 GCC Job Radar Chrome Extension Bridge running on http://127.0.0.1:8765
   - Health check:     http://127.0.0.1:8765/health
   - PDF output dir:   builder/output/
   - Tectonic engine:  tools/bin/tectonic.exe
```

---

### Step 2: Load Extension in Google Chrome
1. Open Google Chrome and navigate to:
   ```text
   chrome://extensions
   ```
2. Enable **Developer mode** toggle in the top-right corner.
3. Click the **Load unpacked** button in the top-left (or click **Reload** if already installed).
4. Select the directory:
   ```text
   D:\Projects\gcc-job-radar\chrome-extension
   ```
5. Pin the **GCC Job Radar** extension to your Chrome toolbar.

---

## 📖 How to Use

1. **Browse to any Job Posting**:
   - Open any job listing on Ashby, LinkedIn, Wellfound, Naukri, or company career portals.
2. **Open the Side Panel**:
   - Click the GCC Job Radar icon in your browser toolbar. The extension opens docked in your Chrome **Side Panel** on the right.
   - The extension automatically extracts the **Company**, **Role**, and **Job Description**.
   - *(Tip: You can also highlight any text on the page, right-click, and choose "Evaluate & Tailor Resume for Selection").*
3. **Evaluate**:
   - Click **⚡ Evaluate Fit**.
   - In 2 seconds, you get a score (e.g. `95/100`), verdict badge (`APPLY`), matched skills pill tags, and specific recommendation advice.
4. **Tailor & Download PDF**:
   - Click **✨ Tailor Resume**.
   - The Groq engine tailors the LaTeX bullet points and skills ordering.
   - Tectonic compiles the PDF in ~1.4 seconds.
   - Preview the PDF right in the side panel, request tweaks, or click **Looks Good, Download PDF**.
5. **Mark Applied**:
   - Click **✅ Mark Applied in Radar** to log the application in `gcc_jobs.db`.
