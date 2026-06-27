/* ═══════════════════════════════════════════
   CodeLens AI — Application Logic
   ═══════════════════════════════════════════ */

(() => {
  "use strict";

  // ── DOM refs ──
  const $  = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  const inputSection   = $("#input-section");
  const resultsSection = $("#results-section");
  const form           = $("#analyze-form");
  const btnAnalyze     = $("#btn-analyze");
  const btnBack        = $("#btn-back");

  const navAnalyze     = $("#nav-analyze");
  const navBulk        = $("#nav-bulk");
  const navRecords     = $("#nav-records");
  const recordsSection = $("#records-section");
  const bulkSection    = $("#bulk-section");

  let apiResponse = null;  // store full response for export

  // ── Platform metadata ──
  const PLATFORMS = {
    leetcode:       { label: "LeetCode",      emoji: "🟡", color: "#f59e0b" },
    codeforces:     { label: "Codeforces",    emoji: "🔵", color: "#3b82f6" },
    codechef:       { label: "CodeChef",      emoji: "🟣", color: "#a855f7" },
    hackerrank:     { label: "HackerRank",    emoji: "🟢", color: "#10b981" },
    geeksforgeeks:  { label: "GeeksForGeeks", emoji: "🌿", color: "#2f8d46" },
    atcoder:        { label: "AtCoder",       emoji: "🔷", color: "#06b6d4" },
    spoj:           { label: "SPOJ",          emoji: "🔴", color: "#ef4444" },
    hackerearth:    { label: "HackerEarth",   emoji: "🟠", color: "#f97316" },
  };

  // ── Helpers ──
  function extractUsername(url) {
    if (!url) return null;
    try {
      const parts = url.replace(/\/+$/, "").split("/");
      let last = parts.pop();
      if (last && last.startsWith("@")) last = last.slice(1);
      // skip common path segments
      if (["u", "profile", "users"].includes(last)) last = parts.pop();
      return last || null;
    } catch { return null; }
  }

  function fmt(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") return v.toLocaleString();
    return String(v);
  }

  function levelClass(level) {
    if (!level) return "level-none";
    const l = level.toLowerCase().replace(/\s+/g, "_");
    const map = { excellent: "level-excellent", strong: "level-strong", moderate: "level-moderate", beginner: "level-beginner", none: "level-none", not_ready: "level-not_ready" };
    return map[l] || "level-none";
  }

  function scoreColor(val) {
    if (val >= 80) return "#10b981";
    if (val >= 60) return "#3b82f6";
    if (val >= 40) return "#f59e0b";
    if (val >= 20) return "#f97316";
    return "#ef4444";
  }

  // ── Toast system ──
  function toast(message, type = "success") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-icon">${type === "success" ? "✅" : "❌"}</span>${message}`;
    const container = $("#toast-container");
    container.appendChild(el);
    setTimeout(() => {
      el.style.animation = "toast-out .3s ease-in forwards";
      el.addEventListener("animationend", () => el.remove());
    }, 3000);
  }

  // ── Show / hide sections ──
  function hideAllSections() {
    inputSection.classList.add("hidden");
    resultsSection.classList.add("hidden");
    if (recordsSection) recordsSection.classList.add("hidden");
    if (bulkSection) bulkSection.classList.add("hidden");
    if (navAnalyze) navAnalyze.classList.remove("active");
    if (navRecords) navRecords.classList.remove("active");
    if (navBulk) navBulk.classList.remove("active");
  }

  function showResults() {
    hideAllSections();
    resultsSection.classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showInput() {
    hideAllSections();
    inputSection.classList.remove("hidden");
    if (navAnalyze) navAnalyze.classList.add("active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showRecords() {
    hideAllSections();
    if (recordsSection) recordsSection.classList.remove("hidden");
    if (navRecords) navRecords.classList.add("active");
    fetchRecords();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showBulk() {
    hideAllSections();
    if (bulkSection) bulkSection.classList.remove("hidden");
    if (navBulk) navBulk.classList.add("active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  if (navAnalyze) navAnalyze.addEventListener("click", showInput);
  if (navRecords) navRecords.addEventListener("click", showRecords);
  if (navBulk) navBulk.addEventListener("click", showBulk);

  // ── Bulk Upload Logic ──
  const csvUpload = $("#csv-upload");
  if (csvUpload) {
    csvUpload.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      $("#csv-filename").textContent = "Selected: " + file.name;
      
      try {
        let rows = [];
        if (file.name.endsWith('.csv')) {
          const text = await file.text();
          rows = text.split("\n").map(r => r.trim()).filter(r => r);
        } else {
          // Use SheetJS for Excel
          const arrayBuffer = await file.arrayBuffer();
          const workbook = XLSX.read(arrayBuffer, { type: 'array' });
          const firstSheetName = workbook.SheetNames[0];
          const worksheet = workbook.Sheets[firstSheetName];
          const csvText = XLSX.utils.sheet_to_csv(worksheet);
          rows = csvText.split("\n").map(r => r.trim()).filter(r => r);
        }

        if (rows.length < 2) {
          toast("File is empty or invalid.", "error");
          return;
        }
        
        const headers = rows[0].split(",").map(h => h.trim().toLowerCase());
        const studentIdx = headers.indexOf("student_name");
        if (studentIdx === -1) {
          toast("File must contain 'student_name' column.", "error");
          return;
        }

        const queue = [];
        for (let i = 1; i < rows.length; i++) {
          const cols = rows[i].split(",").map(c => c.trim());
          if (!cols[studentIdx]) continue;
          
          const payload = { student_name: cols[studentIdx] };
          let hasPlatform = false;
          Object.keys(PLATFORMS).forEach(p => {
            const idx = headers.indexOf(p);
            if (idx !== -1 && idx < cols.length && cols[idx]) {
              payload[p] = cols[idx];
              hasPlatform = true;
            }
          });
          
          if (hasPlatform) {
            queue.push(payload);
          }
        }
        
        if (queue.length === 0) {
          toast("No valid profiles found to process.", "error");
          return;
        }

        startBulkProcessing(queue);
      } catch (err) {
        toast("Failed to read file: " + err.message, "error");
      }
    });
  }

  const btnDownloadTemplate = $("#btn-download-template");
  if (btnDownloadTemplate) {
    btnDownloadTemplate.addEventListener("click", () => {
      const headers = ["student_name", ...Object.keys(PLATFORMS)];
      const sampleData = ["John Doe", ...Object.keys(PLATFORMS).map(() => "johndoe")];
      
      const csvContent = headers.join(",") + "\n" + sampleData.join(",");
      const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      
      const link = document.createElement("a");
      link.setAttribute("href", url);
      link.setAttribute("download", "developer_profiles_template.csv");
      link.style.visibility = "hidden";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      toast("Template downloaded successfully!", "success");
    });
  }

  let processedResults = [];

  async function startBulkProcessing(queue) {
    $("#bulk-dashboard").style.display = "block";
    $("#analytics-dashboard").style.display = "block";
    $("#bulk-results-container").style.display = "block";
    let processed = 0;
    let total = queue.length;
    processedResults = [];
    $("#bulk-total").textContent = total;
    $("#bulk-processed").textContent = 0;
    $("#bulk-queued").textContent = total;
    
    const liveFeed = $("#live-feed-list");
    const queueFeed = $("#queue-list");
    const bulkResults = $("#bulk-results-tbody");
    
    const updateUI = () => {
      queueFeed.innerHTML = "";
      queue.forEach((q, i) => {
        queueFeed.innerHTML += `<div style="background: rgba(255,255,255,0.05); padding: 8px; border-radius: 4px; font-size: 0.9rem;">⏳ ${q.student_name}</div>`;
      });
      $("#bulk-queued").textContent = queue.length;
      $("#bulk-processed").textContent = processed;
    };
    
    liveFeed.innerHTML = "";
    bulkResults.innerHTML = "";
    updateUI();
    
    while (queue.length > 0) {
      const current = queue.shift();
      updateUI();
      
      const feedItem = document.createElement("div");
      feedItem.style.background = "rgba(59, 130, 246, 0.2)";
      feedItem.style.padding = "8px";
      feedItem.style.borderRadius = "4px";
      feedItem.style.fontSize = "0.9rem";
      feedItem.innerHTML = `🔄 Processing: <b>${current.student_name}</b>...`;
      liveFeed.prepend(feedItem);
      
      try {
        const res = await fetch("/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(current),
        });
        
        if (res.ok) {
          const data = await res.json();
          feedItem.style.background = "rgba(16, 185, 129, 0.2)";
          feedItem.innerHTML = `✅ Completed: <b>${current.student_name}</b>`;
          
          const scores = data.scores || {};
          const evalData = data.evaluation || {};
          
          let cfScore = "—";
          let lcScore = "—";
          let ccScore = "—";
          let hrScore = "—";
          let gfgScore = "—";
          
          const profiles = data.profiles || [];
          for (const p of profiles) {
            if (p.platform === "Codeforces") {
              cfScore = `${p.solved_count || 0} solved`;
              if (p.rating) cfScore += ` (Rating: ${p.rating})`;
            } else if (p.platform === "LeetCode") {
              lcScore = `${p.solved_count || 0} solved`;
              if (p.percentile) lcScore += ` (${p.percentile}%)`;
            } else if (p.platform === "CodeChef") {
              ccScore = `${p.solved_count || 0} solved`;
              if (p.rating) ccScore += ` (Rating: ${p.rating})`;
            } else if (p.platform === "HackerRank") {
              hrScore = `${p.solved_count || 0} solved`;
            } else if (p.platform === "GeeksForGeeks") {
              gfgScore = `${p.solved_count || 0} solved`;
            }
          }
          
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td style="font-weight: 500;">${current.student_name}</td>
            <td>
              <div style="display: flex; align-items: center; gap: 8px;">
                <div class="score-pill" style="background: ${scoreColor(scores.overall_score)}; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: bold; font-size: 0.9rem;">
                  ${fmt(scores.overall_score)}
                </div>
              </div>
            </td>
            <td>${lcScore}</td>
            <td>${cfScore}</td>
            <td>${ccScore}</td>
            <td>${hrScore}</td>
            <td>${gfgScore}</td>
            <td>
              <button class="action-btn" onclick="window.viewRecord('${current.student_name}')">View Details</button>
            </td>
          `;
          bulkResults.appendChild(tr);
          
          processedResults.push({ name: current.student_name, data: data });
          updateAnalyticsDashboard();
        } else {
          feedItem.style.background = "rgba(239, 68, 68, 0.2)";
          feedItem.innerHTML = `❌ Failed: <b>${current.student_name}</b>`;
        }
      } catch (err) {
        feedItem.style.background = "rgba(239, 68, 68, 0.2)";
        feedItem.innerHTML = `❌ Error: <b>${current.student_name}</b>`;
      }
      
      processed++;
      updateUI();
    }
    
    toast("Bulk processing complete!", "success");
  }

  // ── Form submit ──
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      student_name: $("#student_name").value.trim()
    };
    Object.keys(PLATFORMS).forEach((k) => {
      const val = $(`#${k}`).value.trim();
      body[k] = val || null;
    });

    if (!body.student_name) {
      toast("Student Name is required", "error");
      return;
    }

    // At least one URL required
    if (Object.values(body).every((v) => !v)) {
      toast("Please enter at least one profile URL.", "error");
      return;
    }

    btnAnalyze.classList.add("loading");
    btnAnalyze.disabled = true;

    try {
      const res = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${res.status}`);
      }

      apiResponse = await res.json();
      renderResults(apiResponse);
      showResults();
      fetchRecords(); // Refresh sidebar after successful save
    } catch (err) {
      toast(err.message || "Analysis failed. Please try again.", "error");
    } finally {
      btnAnalyze.classList.remove("loading");
      btnAnalyze.disabled = false;
    }
  });

  // ── Back ──
  btnBack.addEventListener("click", showInput);

  // ── Records Table ──
  async function fetchRecords() {
    try {
      const res = await fetch("/records");
      const data = await res.json();
      renderRecordsTable(data.records || []);
    } catch (e) {
      console.error("Failed to fetch records", e);
    }
  }

  function renderRecordsTable(records) {
    const tbody = $("#records-tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    
    if (records.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 30px; color: rgba(255,255,255,0.5);">No records found. Enter a student to get started.</td></tr>`;
      return;
    }

    records.forEach(r => {
      const tr = document.createElement("tr");
      const d = new Date(r.timestamp);
      const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
      
      tr.innerHTML = `
        <td style="font-weight: 600;">${r.name}</td>
        <td style="color: #a1a1aa;">${dateStr}</td>
        <td><span style="color: ${scoreColor(r.overall_score)}; font-weight:bold;">${r.overall_score || "—"}</span></td>
        <td><span class="${levelClass(r.dsa_strength)}">${r.dsa_strength || "—"}</span></td>
        <td><span class="${levelClass(r.cp_level)}">${r.cp_level || "—"}</span></td>
        <td>${r.leetcode_percentile ? r.leetcode_percentile + "%" : "—"}</td>
        <td>${r.codeforces || "—"}</td>
        <td><button class="btn-view" data-name="${r.name}">View Report</button></td>
      `;
      tbody.appendChild(tr);
    });

    // Add click event for view buttons
    tbody.querySelectorAll(".btn-view").forEach(btn => {
      btn.addEventListener("click", () => {
        loadRecord(btn.dataset.name);
      });
    });
  }

  async function loadRecord(name) {
    try {
      const res = await fetch("/records/" + encodeURIComponent(name));
      if (!res.ok) throw new Error("Failed to load record");
      const data = await res.json();
      apiResponse = data;
      renderResults(data);
      showResults();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  window.viewRecord = loadRecord;

  // Load records on startup
  fetchRecords();



  // ── Render results ──
  function renderResults(data) {
    renderPlatformCards(data.profiles || []);
    renderRadar(data.radar || {});
    renderDifficulty(data.profiles || []);
    renderScores(data.scores || {});
    renderEvaluation(data.evaluation || {});
    renderCPScoring(data.cp_scoring || null);
    renderAnalysis(data.analysis || {});
  }

  // ── Platform cards ──
  function renderPlatformCards(profiles) {
    const container = $("#platform-cards");
    container.innerHTML = "";

    profiles.forEach((p) => {
      const key = p.platform.toLowerCase().replace(/\s+/g, "");
      const meta = PLATFORMS[key] || { label: p.platform, emoji: "📌", color: "#888" };
      const username = extractUsername(p.profile_url);

      let statsHTML = "";
      statsHTML += statBlock("Total Solved", fmt(p.solved_count));
      if (p.contest_rating != null) statsHTML += statBlock("Contest Rating", fmt(p.contest_rating));
      else if (p.rating != null) statsHTML += statBlock("Rating", fmt(p.rating));
      if (p.rank != null) statsHTML += statBlock("Rank", typeof p.rank === "number" ? `#${p.rank.toLocaleString()}` : p.rank, true);
      if (p.percentile != null) statsHTML += statBlock("Percentile", `${p.percentile}%`);

      let diffHTML = "";
      if (p.problems_by_difficulty) {
        const d = p.problems_by_difficulty;
        if (d.Easy != null) diffHTML += `<span class="diff-pill diff-easy">Easy ${d.Easy}</span>`;
        if (d.Medium != null) diffHTML += `<span class="diff-pill diff-medium">Med ${d.Medium}</span>`;
        if (d.Hard != null) diffHTML += `<span class="diff-pill diff-hard">Hard ${d.Hard}</span>`;
      }

      let statusHTML = "";
      if (p.recent_status_distribution) {
        const entries = Object.entries(p.recent_status_distribution);
        statusHTML = `<div class="diff-pills">${entries.map(([k, v]) => {
          const cls = k === "OK" ? "diff-easy" : k === "WRONG_ANSWER" ? "diff-hard" : "diff-medium";
          return `<span class="diff-pill ${cls}">${k.replace(/_/g, " ")} ${v}</span>`;
        }).join("")}</div>`;
      }

      const card = document.createElement("div");
      card.className = "glass-card plat-card";
      card.innerHTML = `
        <div class="plat-header">
          <div class="plat-name">
            <span class="plat-emoji">${meta.emoji}</span>
            ${meta.label}
          </div>
          <span class="plat-badge">Connected</span>
        </div>
        ${username ? `<div class="plat-user">@<strong>${username}</strong></div>` : ""}
        <div class="plat-stats">${statsHTML}</div>
        ${diffHTML ? `<div class="diff-pills">${diffHTML}</div>` : ""}
        ${statusHTML}
        ${p.profile_url ? `<a href="${p.profile_url}" target="_blank" rel="noopener" class="plat-link">
          View Profile
          <svg viewBox="0 0 16 16" fill="none"><path d="M6 3h7v7M13 3L6 10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>` : ""}
      `;
      container.appendChild(card);
    });
  }

  function statBlock(label, value, isSmall = false) {
    return `<div class="stat-item"><div class="stat-label">${label}</div><div class="stat-value${isSmall ? " small" : ""}">${value}</div></div>`;
  }

  // ── Bar chart ──
  let radarChart = null;
  function renderRadar(radar) {
    const ctx = $("#radar-chart").getContext("2d");
    if (radarChart) radarChart.destroy();

    const labels = ["DSA", "CP", "Open Source", "Consistency", "Interview"];
    const values = [
      radar.dsa ?? 0,
      radar.cp ?? 0,
      radar.open_source ?? 0,
      radar.consistency ?? 0,
      radar.interview ?? 0,
    ];

    radarChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Skill Level",
          data: values,
          backgroundColor: [
            "rgba(34,211,238,.7)",
            "rgba(59,130,246,.7)",
            "rgba(168,85,247,.7)",
            "rgba(16,185,129,.7)",
            "rgba(245,158,11,.7)"
          ],
          borderWidth: 0,
          borderRadius: 4
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        scales: {
          y: {
            beginAtZero: true,
            max: 100,
            ticks: { color: "rgba(255,255,255,.5)" },
            grid: { color: "rgba(255,255,255,.05)" }
          },
          x: {
            ticks: { color: "rgba(255,255,255,.8)" },
            grid: { display: false }
          }
        },
        plugins: {
          legend: { display: false },
        },
      },
    });
  }

  // ── Difficulty Doughnut Chart ──
  let diffChart = null;
  function renderDifficulty(profiles) {
    const ctx = $("#difficulty-chart").getContext("2d");
    if (diffChart) diffChart.destroy();

    let easy = 0, medium = 0, hard = 0;
    profiles.forEach(p => {
      if (p.problems_by_difficulty) {
        easy += p.problems_by_difficulty.Easy || 0;
        medium += p.problems_by_difficulty.Medium || 0;
        hard += p.problems_by_difficulty.Hard || 0;
      }
    });

    // If no data, just show a grey ring or nothing. But let's handle zero case
    const hasData = easy + medium + hard > 0;
    
    diffChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Easy", "Medium", "Hard"],
        datasets: [{
          data: hasData ? [easy, medium, hard] : [1],
          backgroundColor: hasData ? [
            "rgba(16,185,129,.8)", // Green
            "rgba(245,158,11,.8)", // Yellow
            "rgba(239,68,68,.8)"   // Red
          ] : ["rgba(255,255,255,0.05)"],
          borderWidth: 0,
          hoverOffset: 4
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "70%",
        plugins: {
          legend: {
            position: "right",
            labels: {
              color: "rgba(255,255,255,.7)",
              font: { size: 12, family: "'Inter', sans-serif" },
              usePointStyle: true,
              padding: 20
            }
          },
          tooltip: {
            enabled: hasData,
            backgroundColor: "rgba(0,0,0,0.8)",
            padding: 10,
            cornerRadius: 8,
            titleFont: { size: 13 },
            bodyFont: { size: 14, weight: "bold" }
          }
        }
      }
    });
  }

  let bulkDoughnutChart = null;
  let bulkBarChart = null;

  function updateAnalyticsDashboard() {
    if (processedResults.length === 0) return;
    
    // 1. Summary Cards
    let sumOverall = 0, sumLeetCode = 0, sumCP = 0;
    let countLC = 0, countCP = 0;
    
    let excellent = 0, good = 0, average = 0, needsWork = 0;

    processedResults.forEach(res => {
      const overall = res.data.scores?.overall_score || 0;
      sumOverall += overall;
      
      // Buckets
      if (overall >= 90) excellent++;
      else if (overall >= 70) good++;
      else if (overall >= 50) average++;
      else needsWork++;

      const profiles = res.data.profiles || [];
      profiles.forEach(p => {
        if (p.platform === "LeetCode") {
          sumLeetCode += (p.percentile || 0);
          countLC++;
        }
        if (["Codeforces", "CodeChef"].includes(p.platform)) {
          sumCP += (p.rating || 0);
          countCP++;
        }
      });
    });

    const total = processedResults.length;
    $("#stat-total").textContent = total;
    $("#stat-leetcode").textContent = countLC ? Math.round(sumLeetCode / countLC) : "—";
    $("#stat-cp").textContent = countCP ? Math.round(sumCP / countCP) : "—";
    $("#stat-overall").textContent = Math.round(sumOverall / total);

    // 2. Distributions
    const pct = (val) => Math.round((val / total) * 100) + "%";
    
    $("#dist-bar-excellent").style.width = pct(excellent);
    $("#dist-count-excellent").textContent = `${excellent} devs`;
    
    $("#dist-bar-good").style.width = pct(good);
    $("#dist-count-good").textContent = `${good} devs`;
    
    $("#dist-bar-average").style.width = pct(average);
    $("#dist-count-average").textContent = `${average} devs`;
    
    $("#dist-bar-needs-work").style.width = pct(needsWork);
    $("#dist-count-needs-work").textContent = `${needsWork} devs`;

    // 3. Key Insights
    const insightsContainer = $("#key-insights-container");
    insightsContainer.innerHTML = "";
    
    const topPerformersPct = Math.round((excellent + good) / total * 100);
    insightsContainer.innerHTML += `
      <div class="insight-item" style="background: rgba(16, 185, 129, 0.05); border-color: rgba(16, 185, 129, 0.2);">
        <div class="insight-icon" style="color: #10b981;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg></div>
        <div class="insight-content">
          <h4>Top Performers</h4>
          <p>${topPerformersPct}% of developers scored 70+ overall.</p>
        </div>
      </div>
    `;

    if (needsWork > 0) {
      insightsContainer.innerHTML += `
        <div class="insight-item" style="background: rgba(245, 158, 11, 0.05); border-color: rgba(245, 158, 11, 0.2);">
          <div class="insight-icon" style="color: #f59e0b;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg></div>
          <div class="insight-content">
            <h4>Development Opportunity</h4>
            <p>${needsWork} developers need support to improve their CP and DSA skills.</p>
          </div>
        </div>
      `;
    }

    // 4. Top Performers Cards
    const sorted = [...processedResults].sort((a, b) => (b.data.scores?.overall_score || 0) - (a.data.scores?.overall_score || 0)).slice(0, 3);
    const topContainer = $("#top-performers-container");
    topContainer.innerHTML = "";
    
    sorted.forEach((dev, index) => {
      let lcStat = "—";
      let cpStat = "—";
      dev.data.profiles?.forEach(p => {
        if (p.platform === "LeetCode") lcStat = p.percentile ? `${p.percentile}%` : "—";
        if (["Codeforces", "CodeChef"].includes(p.platform) && cpStat === "—") cpStat = p.rating || "—";
      });
      
      const score = dev.data.scores?.overall_score || 0;
      const rankColor = index === 0 ? "#f59e0b" : index === 1 ? "#94a3b8" : "#d97706";
      
      topContainer.innerHTML += `
        <div class="glass-card" style="padding: 16px;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px;">
            <div style="width: 28px; height: 28px; border-radius: 50%; background: ${rankColor}; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 0.9rem;">
              ${index + 1}
            </div>
            <div>
              <h4 style="margin: 0; font-size: 1.05rem;">${dev.name}</h4>
            </div>
          </div>
          
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <span style="font-size: 0.85rem; color: #a1a1aa;">Overall Score</span>
            <span style="font-weight: bold; color: ${scoreColor(score)}">${fmt(score)}/100</span>
          </div>
          <div style="width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; margin-bottom: 12px; overflow: hidden;">
            <div style="height: 100%; width: ${score}%; background: ${scoreColor(score)}; border-radius: 3px;"></div>
          </div>
          
          <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: #a1a1aa;">
            <span>LeetCode: ${lcStat}</span>
            <span>CP Rating: ${cpStat}</span>
          </div>
        </div>
      `;
    });

    // 5. Chart.js visual rendering
    try {
      const doughnutCtx = $("#bulk-doughnut-chart").getContext("2d");
      if (bulkDoughnutChart) bulkDoughnutChart.destroy();
      bulkDoughnutChart = new Chart(doughnutCtx, {
        type: "doughnut",
        data: {
          labels: ["Excellent", "Good", "Average", "Needs Work"],
          datasets: [{
            data: [excellent, good, average, needsWork],
            backgroundColor: ["#10b981", "#3b82f6", "#f59e0b", "#ef4444"],
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "70%",
          plugins: {
            legend: {
              position: "right",
              labels: {
                color: "rgba(255,255,255,.7)",
                font: { size: 10, family: "'Inter', sans-serif" },
                usePointStyle: true,
                padding: 10
              }
            }
          }
        }
      });
    } catch (e) {
      console.error("Failed to render bulk doughnut chart", e);
    }

    try {
      const barCtx = $("#bulk-bar-chart").getContext("2d");
      if (bulkBarChart) bulkBarChart.destroy();
      
      let sumLC_solved = 0, countLC_solved = 0;
      let sumCF_solved = 0, countCF_solved = 0;
      let sumCC_solved = 0, countCC_solved = 0;
      let sumHR_solved = 0, countHR_solved = 0;
      let sumGFG_solved = 0, countGFG_solved = 0;
      
      processedResults.forEach(res => {
        const profiles = res.data.profiles || [];
        profiles.forEach(p => {
          if (p.platform === "LeetCode") {
            sumLC_solved += (p.solved_count || 0);
            countLC_solved++;
          } else if (p.platform === "Codeforces") {
            sumCF_solved += (p.solved_count || 0);
            countCF_solved++;
          } else if (p.platform === "CodeChef") {
            sumCC_solved += (p.solved_count || 0);
            countCC_solved++;
          } else if (p.platform === "HackerRank") {
            sumHR_solved += (p.solved_count || 0);
            countHR_solved++;
          } else if (p.platform === "GeeksForGeeks") {
            sumGFG_solved += (p.solved_count || 0);
            countGFG_solved++;
          }
        });
      });
      
      const avgLC = countLC_solved ? Math.round(sumLC_solved / countLC_solved) : 0;
      const avgCF = countCF_solved ? Math.round(sumCF_solved / countCF_solved) : 0;
      const avgCC = countCC_solved ? Math.round(sumCC_solved / countCC_solved) : 0;
      const avgHR = countHR_solved ? Math.round(sumHR_solved / countHR_solved) : 0;
      const avgGFG = countGFG_solved ? Math.round(sumGFG_solved / countGFG_solved) : 0;

      bulkBarChart = new Chart(barCtx, {
        type: "bar",
        data: {
          labels: ["LeetCode", "Codeforces", "CodeChef", "HackerRank", "GeeksForGeeks"],
          datasets: [{
            label: "Avg Solved",
            data: [avgLC, avgCF, avgCC, avgHR, avgGFG],
            backgroundColor: ["#f59e0b", "#3b82f6", "#a855f7", "#10b981", "#2f8d46"],
            borderWidth: 0,
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              beginAtZero: true,
              ticks: { color: "rgba(255,255,255,.5)", font: { size: 9 } },
              grid: { color: "rgba(255,255,255,.05)" }
            },
            x: {
              ticks: { color: "rgba(255,255,255,.8)", font: { size: 9 } },
              grid: { display: false }
            }
          },
          plugins: {
            legend: { display: false }
          }
        }
      });
    } catch (e) {
      console.error("Failed to render bulk bar chart", e);
    }
  }

  // ── CP-Agent Score Breakdown (Explainable AI) ──
  function renderCPScoring(cpScoring) {
    const card = document.getElementById('cp-scoring-card');
    if (!cpScoring || !cpScoring.platform_scores || cpScoring.platform_scores.length === 0) {
      if (card) card.style.display = 'none';
      return;
    }
    card.style.display = '';

    const badge = document.getElementById('cp-score-badge');
    if (badge) {
      badge.textContent = `${cpScoring.score_tier} · ${cpScoring.final_score}/100`;
    }

    const methodEl = document.getElementById('cp-aggregation-method');
    if (methodEl) methodEl.textContent = cpScoring.aggregation_method;

    const reasoningEl = document.getElementById('cp-overall-reasoning');
    if (reasoningEl) reasoningEl.textContent = cpScoring.overall_reasoning;

    const container = document.getElementById('cp-platform-scores');
    if (!container) return;
    container.innerHTML = '';

    const platColors = {
      LeetCode: '#f59e0b',
      Codeforces: '#3b82f6',
      CodeChef: '#a855f7',
      HackerRank: '#10b981',
      GeeksForGeeks: '#2f8d46',
    };

    cpScoring.platform_scores.forEach(ps => {
      const col = platColors[ps.platform] || '#888';
      const weightPct = Math.round(ps.weight * 100);

      function subBar(label, sub, emoji) {
        const sc = sub.score;
        const barCol = sc >= 70 ? '#10b981' : sc >= 45 ? '#f59e0b' : '#ef4444';
        return `
          <div style="margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
              <span style="font-size:0.85rem; color:#d4d4d8;">${emoji} ${label}</span>
              <span style="font-weight:700; color:${barCol};">${sc.toFixed(1)}/100</span>
            </div>
            <div style="width:100%; height:6px; background:rgba(255,255,255,0.08); border-radius:3px; margin-bottom:6px; overflow:hidden;">
              <div style="height:100%; width:${sc}%; background:${barCol}; border-radius:3px; transition:width 0.6s ease;"></div>
            </div>
            <div style="font-size:0.78rem; color:#71717a; line-height:1.5; font-style:italic;">
              <strong style="color:#a1a1aa; font-style:normal;">Formula:</strong> ${sub.formula}<br/>
              ${sub.reasoning}
            </div>
          </div>
        `;
      }

      const el = document.createElement('div');
      el.style.cssText = `border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:18px; background:rgba(255,255,255,0.02); position:relative; overflow:hidden;`;
      el.innerHTML = `
        <div style="position:absolute; top:0; left:0; width:4px; height:100%; background:${col}; border-radius:12px 0 0 12px;"></div>
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:16px; padding-left:12px;">
          <div>
            <h4 style="margin:0; font-size:1rem; color:#fff;">${ps.platform}</h4>
            <span style="font-size:0.8rem; color:#71717a;">Weight: ${weightPct}% of final score</span>
          </div>
          <div style="text-align:right;">
            <div style="font-size:1.6rem; font-weight:800; color:${col};">${ps.platform_score.toFixed(1)}</div>
            <div style="font-size:0.75rem; color:#71717a;">Platform Score</div>
          </div>
        </div>
        <div style="padding-left:12px;">
          ${subBar('Clout — Elite Standing & Hard Depth', ps.clout, '🏆')}
          ${subBar('Consistency — Temporal Habit & Discipline', ps.consistency, '📅')}
          ${subBar('Velocity — Accuracy & Edge-Case Awareness', ps.velocity, '⚡')}
        </div>
        <div style="padding-left:12px; margin-top:10px; padding:10px 12px; background:rgba(255,255,255,0.04); border-radius:8px; font-size:0.82rem; color:#a1a1aa;">
          💬 ${ps.reasoning}
        </div>
      `;
      container.appendChild(el);
    });
  }

  // ── Scores ──
  function renderScores(scores) {
    const grid = $("#scores-grid");
    grid.innerHTML = "";

    // Overall score tile
    const overall = scores.overall_score ?? 0;
    grid.innerHTML += `
      <div class="score-tile overall">
        <div class="score-number" style="color:${scoreColor(overall)}">${overall}</div>
        <div class="score-label">Overall Score</div>
      </div>
    `;

    const items = [
      ["DSA Strength", scores.dsa_strength],
      ["Competitive Programming", scores.competitive_programming],
      ["Open Source", scores.open_source],
      ["Interview Readiness", scores.interview_readiness],
      ["FAANG Readiness", scores.faang_readiness],
    ];

    items.forEach(([label, level]) => {
      const lv = level || "none";
      const displayLevel = lv.replace(/_/g, " ");
      grid.innerHTML += `
        <div class="score-tile">
          <div class="score-label">${label}</div>
          <span class="score-level ${levelClass(lv)}">${displayLevel}</span>
        </div>
      `;
    });
  }

  // ── Evaluation ──
  function renderEvaluation(evaluation) {
    const card = $("#eval-card");
    const content = $("#eval-content");

    const hasContent = evaluation.leetcode_percentile != null ||
                       evaluation.consistency_score != null ||
                       (evaluation.code_red_flags && evaluation.code_red_flags.length);

    if (!hasContent) { card.style.display = "none"; return; }
    card.style.display = "";

    let html = '<div class="eval-row">';
    if (evaluation.leetcode_percentile != null) {
      html += `<span class="eval-chip eval-stat">LeetCode Percentile: <span class="eval-val">${evaluation.leetcode_percentile}%</span></span>`;
    }
    if (evaluation.consistency_score != null) {
      html += `<span class="eval-chip eval-stat">Consistency: <span class="eval-val">${evaluation.consistency_score}</span></span>`;
    }
    html += "</div>";

    if (evaluation.code_red_flags?.length) {
      html += '<div class="eval-row">';
      evaluation.code_red_flags.forEach((f) => {
        html += `<span class="eval-chip flag">⚠️ ${f}</span>`;
      });
      html += "</div>";
    }

    content.innerHTML = html;
  }

  // ── Analysis ──
  function renderAnalysis(analysis) {
    const container = $("#analysis-content");
    container.innerHTML = "";

    const sections = [
      { icon: "💪", title: "Strengths",          items: analysis.strengths },
      { icon: "⚠️", title: "Weaknesses",         items: analysis.weaknesses },
      { icon: "📚", title: "Recommended Topics",  items: analysis.recommended_topics },
      { icon: "🎯", title: "Next Steps",          items: analysis.next_steps },
    ];

    sections.forEach(({ icon, title, items }) => {
      if (!items?.length) return;
      const block = document.createElement("div");
      block.className = "analysis-block";
      block.innerHTML = `
        <div class="analysis-block-title">${icon} ${title}</div>
        <ul class="analysis-list">${items.map((i) => `<li>${i}</li>`).join("")}</ul>
      `;
      container.appendChild(block);
    });

    if (analysis.personalized_feedback) {
      const fb = document.createElement("div");
      fb.className = "feedback-box";
      fb.innerHTML = `<div class="feedback-label">💬 Personalized Feedback</div>${analysis.personalized_feedback}`;
      container.appendChild(fb);
    }
  }
})();
