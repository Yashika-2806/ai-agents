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
  const btnCopy        = $("#btn-copy");
  const btnDownload    = $("#btn-download");
  const btnPdf         = $("#btn-pdf");

  let apiResponse = null;  // store full response for export

  // ── Platform metadata ──
  const PLATFORMS = {
    leetcode:   { label: "LeetCode",   emoji: "🟡", color: "#f59e0b" },
    codeforces: { label: "Codeforces", emoji: "🔵", color: "#3b82f6" },
    codechef:   { label: "CodeChef",   emoji: "🟣", color: "#a855f7" },
    hackerrank: { label: "HackerRank", emoji: "🟢", color: "#10b981" },
    atcoder:    { label: "AtCoder",    emoji: "🔷", color: "#06b6d4" },
    spoj:       { label: "SPOJ",       emoji: "🔴", color: "#ef4444" },
    hackerearth:{ label: "HackerEarth",emoji: "🟠", color: "#f97316" },
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
  function showResults() {
    inputSection.classList.add("hidden");
    resultsSection.classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showInput() {
    resultsSection.classList.add("hidden");
    inputSection.classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ── Form submit ──
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {};
    Object.keys(PLATFORMS).forEach((k) => {
      const val = $(`#${k}`).value.trim();
      body[k] = val || null;
    });

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
    } catch (err) {
      toast(err.message || "Analysis failed. Please try again.", "error");
    } finally {
      btnAnalyze.classList.remove("loading");
      btnAnalyze.disabled = false;
    }
  });

  // ── Back ──
  btnBack.addEventListener("click", showInput);


  // ── Render results ──
  function renderResults(data) {
    renderPlatformCards(data.profiles || []);
    renderRadar(data.radar || {});
    renderScores(data.scores || {});
    renderEvaluation(data.evaluation || {});
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
