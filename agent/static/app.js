const analyzeButton = document.getElementById("analyze");
const message = document.getElementById("message");
const resultGrid = document.getElementById("result-grid");
const evaluationGrid = document.getElementById("evaluation");

const createDetailItem = (label, value) => `
  <li><strong>${label}:</strong> <span>${value}</span></li>
`;

const setMessage = (text, type = "info") => {
  message.textContent = text;
  message.className = `message ${type}`;
};

const renderDetailSection = (title, items) => {
  if (!items || !items.length) return "";
  return `
    <div class="detail-section">
      <h4>${title}</h4>
      <ul class="detail-list">
        ${items.map((item) => `<li>${item}</li>`).join("")}
      </ul>
    </div>
  `;
};

const renderProfileCard = (profile) => {
  const difficultyItems = profile.problems_by_difficulty
    ? Object.entries(profile.problems_by_difficulty).map(([key, count]) => `${key}: ${count}`)
    : [];

  return `
    <article class="result-card">
      <h3>${profile.platform}</h3>
      <p><a href="${profile.profile_url}" target="_blank" rel="noreferrer">View profile</a></p>
      <ul class="detail-list">
        ${createDetailItem("Solved", profile.solved_count ?? "—")}
        ${createDetailItem("Rank", profile.rank ?? "—")}
        ${createDetailItem("Percentile", profile.percentile != null ? `${profile.percentile}%` : "—")}
        ${createDetailItem("Rating", profile.rating ?? "—")}
        ${createDetailItem("Contest rating", profile.contest_rating ?? "—")}
      </ul>
      ${renderDetailSection("Difficulty breakdown", difficultyItems)}
    </article>
  `;
};

const computeSummaryMetrics = (profiles) => {
  const numericRatings = profiles
    .map((profile) => profile.rating)
    .filter((value) => typeof value === "number");

  const numericContest = profiles
    .map((profile) => profile.contest_rating)
    .filter((value) => typeof value === "number");

  const totalSolved = profiles.reduce(
    (sum, profile) => sum + (typeof profile.solved_count === "number" ? profile.solved_count : 0),
    0,
  );

  const validProfiles = profiles.length;
  return {
    totalProfiles: validProfiles,
    totalSolved,
    averageRating: numericRatings.length ? Math.round(numericRatings.reduce((sum, value) => sum + value, 0) / numericRatings.length) : null,
    maxRating: numericRatings.length ? Math.max(...numericRatings) : null,
    averageContestRating: numericContest.length
      ? Math.round(numericContest.reduce((sum, value) => sum + value, 0) / numericContest.length)
      : null,
    ratedProfiles: numericRatings.length,
  };
};

const renderEvaluationCard = (evaluation, profiles) => {
  const summary = computeSummaryMetrics(profiles);
  const flags = evaluation.code_red_flags.length
    ? `<ul class="detail-list">${evaluation.code_red_flags.map((flag) => `<li>${flag}</li>`).join("")}</ul>`
    : `<p>No red flags detected.</p>`;

  const platformSummaries = profiles
    .map((profile) => {
      const solved = profile.solved_count != null ? profile.solved_count : "—";
      const rating = profile.rating != null ? profile.rating : "—";
      const contest = profile.contest_rating != null ? profile.contest_rating : "—";
      const rank = profile.rank != null ? profile.rank : "—";
      return `<li><strong>${profile.platform}</strong>: solved ${solved}, rating ${rating}, contest ${contest}, rank ${rank}</li>`;
    })
    .join("");

  return `
    <article class="evaluation-card">
      <h3>Profile Insight</h3>
      <div class="metrics">
        <div class="metric"><strong>LeetCode percentile</strong><span>${evaluation.leetcode_percentile != null ? `${evaluation.leetcode_percentile}%` : "—"}</span></div>
        <div class="metric"><strong>Consistency</strong><span>${evaluation.consistency_score}/100</span></div>
        <div class="metric"><strong>Profiles analyzed</strong><span>${summary.totalProfiles}</span></div>
        <div class="metric"><strong>Total solved</strong><span>${summary.totalSolved}</span></div>
        <div class="metric"><strong>Avg rating</strong><span>${summary.averageRating != null ? summary.averageRating : "—"}</span></div>
        <div class="metric"><strong>Max rating</strong><span>${summary.maxRating != null ? summary.maxRating : "—"}</span></div>
        <div class="metric"><strong>Avg contest rating</strong><span>${summary.averageContestRating != null ? summary.averageContestRating : "—"}</span></div>
      </div>
      <div class="detail-section">
        <h4>Platform summaries</h4>
        <ul class="detail-list">${platformSummaries}</ul>
      </div>
      <div class="detail-section">
        <h4>Code red flags</h4>
        ${flags}
      </div>
    </article>
  `;
};

analyzeButton.addEventListener("click", async () => {
  const payload = {
    leetcode: document.getElementById("leetcode").value.trim() || null,
    codeforces: document.getElementById("codeforces").value.trim() || null,
    codechef: document.getElementById("codechef").value.trim() || null,
    hackerrank: document.getElementById("hackerrank").value.trim() || null,
    atcoder: document.getElementById("atcoder").value.trim() || null,
    spoj: document.getElementById("spoj").value.trim() || null,
    hackerearth: document.getElementById("hackerearth").value.trim() || null,
  };

  if (!payload.leetcode && !payload.codeforces && !payload.codechef && !payload.hackerrank && !payload.atcoder && !payload.spoj && !payload.hackerearth) {
    setMessage("Please enter at least one profile link before analyzing.", "warning");
    return;
  }

  resultGrid.innerHTML = "";
  evaluationGrid.innerHTML = "";
  setMessage("Analyzing profiles...", "info");
  analyzeButton.disabled = true;

  try {
    const response = await fetch("/analyze", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const responseText = await response.text();
    let data = null;
    try {
      data = JSON.parse(responseText);
    } catch {
      // fallback when server returns non-JSON error HTML/text
    }

    if (!response.ok) {
      throw new Error(
        (data && (data.detail || data.message)) || responseText || "Failed to analyze profiles",
      );
    }

    if (!data) {
      throw new Error("Invalid JSON returned from analyze endpoint.");
    }

    setMessage("Analysis complete.", "success");

    if (data.profiles && data.profiles.length) {
      data.profiles.forEach((profile) => {
        resultGrid.innerHTML += renderProfileCard(profile);
      });
    } else {
      setMessage("No valid profiles were returned.", "warning");
    }

    if (data.evaluation) {
      evaluationGrid.innerHTML = renderEvaluationCard(data.evaluation, data.profiles || []);
    }
  } catch (err) {
    setMessage(`Network error: ${err.message}`, "error");
  } finally {
    analyzeButton.disabled = false;
  }
});
