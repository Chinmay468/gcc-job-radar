# 🎯 GCC Job Radar & Resume Tailor — Chrome Extension

A Chrome Extension (Manifest V3) designed to evaluate job descriptions on the fly, score alignment with your core engineering stack, and generate 1-click tailored PDF resumes locally via **Tectonic** — **completely eliminating Overleaf and manual LaTeX copying**.

---

## ⚡ Key Capabilities

1. **Auto-Detects Job Postings**:
   - Native deep extractors for **LinkedIn, Wellfound (AngelList), Indeed, Naukri, Greenhouse, Lever, Workday, SmartRecruiters, and Unstop**.
   - Universal DOM fallback: automatically extracts from `<article>`, `<main>`, or any text you manually highlight on the page.
2. **Instant Fit Evaluation**:
   - Runs fast rule checks against Chinmay's profile (Java, Spring Boot, React, Node.js, SQL, 0–2 YOE).
   - Calls Groq AI to calculate fit score (0–100), verdict (`APPLY`, `BORDERLINE`, `SKIP`), matched vs. missing skills, and green flags / hard blocks.
3. **1-Click Local Resume Tailoring & PDF Compilation (Zero Overleaf)**:
   - Prompts Groq to tailor `master_resume.tex` specifically for the target JD.
   - Compiles `.tex` $\to$ `.pdf` directly using standalone [`tools/bin/tectonic.exe`](file:///D:/Projects/gcc-job-radar/tools/bin/tectonic.exe) in **~1.4 seconds**.
   - Triggers native browser download for `Chinmay_Maheshwari_<Company>_<Role>.pdf`.
4. **Direct Radar DB Sync**:
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
3. Click the **Load unpacked** button in the top-left.
4. Select the directory:
   ```text
   D:\Projects\gcc-job-radar\chrome-extension
   ```
5. Pin the **GCC Job Radar** extension to your Chrome toolbar for easy access.

---

## 📖 How to Use

1. **Browse to any Job Posting**:
   - Open any job listing on LinkedIn, Wellfound, Naukri, or any company career page.
2. **Open the Extension**:
   - Click the GCC Job Radar icon in your browser toolbar.
   - The extension automatically extracts the **Company**, **Role**, and **Job Description**.
   - *(Tip: You can also highlight any text on the page to evaluate only the selected snippet).*
3. **Evaluate**:
   - Click **⚡ Evaluate Fit**.
   - In 2 seconds, you get a score (e.g. `95/100`), verdict badge (`APPLY`), matched skills pill tags, and specific recommendation advice.
4. **Tailor & Download PDF**:
   - Click **📄 Tailor & Compile PDF**.
   - The Groq engine tailors the LaTeX bullet points and skills ordering.
   - Tectonic compiles the PDF in ~1.4 seconds.
   - The compiled PDF automatically downloads to your `Downloads` folder!
5. **Mark Applied**:
   - Click **✅ Mark Applied in Radar** to log the application in `gcc_jobs.db`.
