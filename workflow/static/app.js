// Deviantly X Studio front end: plain JS, talks to the FastAPI backend under /api.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  posts: [], selected: null, kind: "quote", status: null, account: null, me: null,
  drafts: [], topics: [],
};

// Solar duotone icons via <iconify-icon> (design.md: icon system only, no emoji).
const I = (name, cls = "") => `<iconify-icon icon="solar:${name}"${cls ? ` class="${cls}"` : ""}></iconify-icon>`;

function emptyState(icon, html, action = "") {
  return `<div class="empty">${I(icon)}<div>${html}</div>${action}</div>`;
}

function skeletonCard(note = "") {
  return `<div class="card skel-card">
    <div class="skeleton sk-avatar"></div>
    <div class="sk-lines"><div class="skeleton sk-line w40"></div><div class="skeleton sk-line w90"></div>
      <div class="skeleton sk-line w75"></div><div class="skeleton sk-line w60"></div></div>
    ${note ? `<div class="skel-note">${esc(note)}</div>` : ""}
  </div>`;
}

// How each kind of draft is named in the UI.
const KINDS = {
  post: { label: "New post", hint: "Share the news in a post of your own", action: "Post now", noun: "post", icon: "pen-new-square-bold-duotone" },
  quote: { label: "Quote", hint: "Repost it with your take on top", action: "Post now", noun: "quote post", icon: "chat-square-like-bold-duotone" },
  reply: { label: "Reply", hint: "Comment under their post", action: "Reply now", noun: "reply", icon: "chat-round-line-bold-duotone" },
  repost: { label: "Repost", hint: "Share it as it is", action: "Repost now", noun: "repost", icon: "repeat-bold-duotone" },
  // Only for your own posts, written when you ask for one (Published tab), never for crawled posts.
  followup: { label: "Follow-up", hint: "Comment under your own post", action: "Comment now", noun: "follow-up comment", icon: "chat-round-dots-bold-duotone", own: true },
};

const STATUS_LABEL = {
  draft: "Draft", scheduled: "Scheduled", publishing: "Posting", posted: "Posted", failed: "Didn't post", rejected: "Rejected",
};

// ------------------------------------------------------------------ api helpers

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Studio": "1" } };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (${res.status})`);
  return data;
}

function toast(msg, bad = false) {
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.innerHTML = `${I(bad ? "danger-triangle-bold-duotone" : "check-circle-bold-duotone")}<span></span>`;
  t.lastElementChild.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 7000 : 3500);
}

async function busy(btn, fn) {
  // Loading state is a shimmer sweep across the pill (design.md: no circular spinners).
  btn.disabled = true;
  btn.classList.add("loading");
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.classList.remove("loading"); }
}

// ------------------------------------------------------------------ formatting

const LINK_RE = /https?:\/\/\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:ai|com|io|co|net|org|app|dev|so|gg|xyz)\b(?:\/\S*)?/gi;
function xLength(text) {  // mirrors drafter.x_length: links count 23, wide characters count 2
  let n = 0;
  const links = text.match(LINK_RE) || [];
  for (const part of text.split(LINK_RE)) for (const ch of part) n += ch.codePointAt(0) > 0x10ff ? 2 : 1;
  return n + 23 * links.length;
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function when(iso) {  // "Fri, Oct 17, 9:30 AM"
  return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function until(iso) {
  const m = Math.round((new Date(iso) - Date.now()) / 60000);
  if (m <= 0) return "any moment now";
  if (m < 60) return `in ${m} min`;
  if (m < 1440) return `in ${Math.floor(m / 60)}h ${m % 60 ? `${m % 60}m` : ""}`.trim();
  const d = Math.round(m / 1440);
  return `in ${d} day${d > 1 ? "s" : ""}`;
}
const num = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K" : String(n ?? 0));
const plural = (n, word) => `${num(n)} ${word}${n === 1 ? "" : "s"}`;
const scoreCls = (s) => (s >= 65 ? "hi" : s >= 50 ? "mid" : "lo");
const fitCls = (f) => (f >= 7 ? "hi" : f >= 4 ? "mid" : "lo");
const linkify = (text) => esc(text).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
const initials = (name) => (name || "?").trim().charAt(0).toUpperCase();
const hue = (s) => [...(s || "")].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const handle = () => (state.me ? `@${state.me}` : "your account");

function avatar(p) {
  return `<div class="avatar" style="background:hsl(${hue(p.username)} 35% 42%)">${esc(initials(p.display_name || p.username))}</div>`;
}
const ourAvatar = () => `<div class="avatar us">D</div>`;
const ourName = () => `<b>Deviantly</b><span>${esc(state.me ? "@" + state.me : "@your_account")}</span>`;

function sourceTweet(p, { stats = true } = {}) {
  return `<div class="tweet">
    ${avatar(p)}
    <div>
      <div class="who"><b>${esc(p.display_name || p.username)}</b><span>@${esc(p.username)} · ${ago(p.created_at)}</span></div>
      <div class="body">${linkify(p.text)}</div>
      ${stats ? `<div class="stats"><span class="metric">${I("chat-round-bold-duotone")}${num(p.replies)}</span><span class="metric">${I("heart-bold-duotone")}${num(p.likes)}</span><a class="metric" href="${esc(p.url)}" target="_blank" rel="noopener">Open on X ${I("arrow-right-up-linear")}</a></div>` : ""}
    </div></div>`;
}

function quotedCard(p) {
  return `<div class="quoted"><div class="tweet"><div>
    <div class="who"><b>${esc(p.display_name || p.username)}</b><span>@${esc(p.username)} · ${ago(p.created_at)}</span></div>
    <div class="body">${linkify(p.text)}</div></div></div></div>`;
}

const statusPill = (status) => `<span class="pill ${status}">${STATUS_LABEL[status] || esc(status)}</span>`;

// ------------------------------------------------------------------ status bar

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
  } catch { return; }
  const s = state.status, c = s.counts || {};
  const chips = [];
  chips.push(s.gemini ? `<span class="chip ok"><span class="dot"></span>Gemini ready</span>` : `<span class="chip bad" title="Add GEMINI_API_KEY to .env"><span class="dot"></span>Gemini key missing</span>`);
  if (!s.x_mode) chips.push(`<span class="chip bad"><span class="dot"></span>X not connected</span>`);
  else chips.push(`<span class="chip ${state.me ? "ok" : ""}"><span class="dot"></span>${state.me ? "Signed in as @" + esc(state.me) : "X account saved"}</span>`);
  if (s.crawl.running) chips.push(`<span class="chip busy"><span class="dot"></span>Finding posts</span>`);
  if (s.autodraft.running) chips.push(`<span class="chip busy"><span class="dot"></span>Writing drafts ${s.autodraft.done} of ${s.autodraft.total}</span>`);
  const ap = s.autopilot;
  if (ap.running) chips.push(`<span class="chip busy"><span class="dot"></span>Autopilot working</span>`);
  else if (ap.enabled) chips.push(`<span class="chip ok" title="${ap.next_run_at ? esc("Next run " + when(ap.next_run_at)) : ""}"><span class="dot"></span>Autopilot on</span>`);
  $("#nAuto").textContent = ap.enabled ? "On" : "";
  if (s.next_scheduled) chips.push(`<span class="chip" title="${esc(when(s.next_scheduled))}">${I("clock-circle-bold-duotone")}Next post ${until(s.next_scheduled)}</span>`);
  $("#chips").innerHTML = chips.join("");
  $("#nReview").textContent = (c.draft || 0) + (c.failed || 0) || "";
  $("#nScheduled").textContent = c.scheduled || "";
  $("#nPublished").textContent = c.posted || "";
  $("#crawlBtn").disabled = s.crawl.running;
  $("#crawlBtn span").textContent = s.crawl.running ? "Finding posts" : "Find new posts";
  $("#crawlBtn").classList.toggle("loading", s.crawl.running);
  renderDrawer();
  renderAutoStatus();
  return s;
}

let lastJobs = { crawl: false, autodraft: false, autopilot: false };
let lastCounts = "";
async function poll() {
  const s = await refreshStatus();
  if (s) {
    // When a job finishes, reload whatever it changed.
    if (lastJobs.crawl && !s.crawl.running) {
      toast(s.crawl.message || "Finished looking for posts", !s.crawl.ok);
      await loadPosts();
    }
    if (lastJobs.autodraft && !s.autodraft.running) {
      toast(s.autodraft.message, s.autodraft.ok === false);
      await Promise.all([loadPosts(), loadDrafts()]);
    } else if (s.autodraft.running) {
      loadDrafts({ background: true });
    }
    // The scheduler publishes on its own; pick up its changes.
    const counts = JSON.stringify(s.counts);
    if (lastCounts && counts !== lastCounts && !s.autodraft.running) loadDrafts({ background: true });
    lastCounts = counts;
    if (lastJobs.autopilot && !s.autopilot.running) {
      toast(s.autopilot.message ? `Autopilot: ${s.autopilot.message}` : "Autopilot finished", s.autopilot.ok === false);
      await Promise.all([loadPosts(), loadDrafts(), loadAutopilot()]);
    } else if (s.autopilot.running && !lastJobs.autopilot) {
      loadAutopilot();
    }
    if ($("#tab-autopilot").classList.contains("active")) renderAutopilotStatus();
    lastJobs = { crawl: s.crawl.running, autodraft: s.autodraft.running, autopilot: s.autopilot.running };
  }
  const busyNow = s && (s.crawl.running || s.autodraft.running || s.autopilot.running);
  setTimeout(poll, busyNow ? 1500 : 8000);
}

// ------------------------------------------------------------------ job log

let drawerDismissed = false;
function renderDrawer() {
  const s = state.status;
  if (!s) return;
  const latest = [s.autopilot, s.autodraft, s.crawl].filter((j) => j.started_at).sort((a, b) => b.started_at.localeCompare(a.started_at));
  const job = s.autopilot.running ? s.autopilot : s.autodraft.running ? s.autodraft : s.crawl.running ? s.crawl : latest[0] || s.crawl;
  const show = (s.crawl.running || s.autodraft.running || s.autopilot.running || job.log.length) && !drawerDismissed;
  $("#drawer").hidden = !show;
  if (!show) return;
  $("#drawerTitle").textContent = job === s.autopilot ? "Autopilot" : job === s.crawl ? "Finding posts" : "Writing drafts";
  $("#drawerMsg").innerHTML = job.running ? `<span class="pill running">Running</span>` : job.ok === false
    ? `<span class="pill failed">Stopped</span> ${esc(job.message)}` : job.message ? `<span class="pill posted">Done</span> ${esc(job.message)}` : "";
  const pre = $("#drawerLog");
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = job.log.join("\n");
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}
$("#drawerClose").onclick = () => { drawerDismissed = true; $("#drawer").hidden = true; };

async function openCrawlDialog() {
  state.topics = await api("/api/topics").catch(() => state.topics);
  $("#cTopics").innerHTML = state.topics.map((t) => `<label class="check"><input type="checkbox" value="${esc(t)}"> ${esc(t)}</label>`).join("");
  $("#crawlDialog").showModal();
}
$("#crawlBtn").onclick = openCrawlDialog;
$("#crawlForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "run") return;
  const body = {
    limit: +$("#cLimit").value || 0,
    since: +$("#cSince").value || 24,
    topics: $$("#cTopics input:checked").map((i) => i.value),
    engines: $("#cEngines").value.replace(/\s+/g, ""),
    autodraft: $("#cAuto").checked ? { count: +$("#cACount").value, kind: $("#cAKind").value, min_score: +$("#cAMin").value } : null,
  };
  try {
    await api("/api/crawl", { method: "POST", body });
    drawerDismissed = false;
    lastJobs.crawl = true;
    toast("Looking for new posts. This takes a few minutes.");
    refreshStatus();
  } catch (err) { toast(err.message, true); }
});

// ------------------------------------------------------------------ posts

async function loadPosts() {
  state.posts = await api("/api/posts");
  $("#nPosts").textContent = state.posts.length || "";
  const topics = [...new Set(state.posts.map((p) => p.topic))].sort();
  const sel = $("#fTopic"), cur = sel.value;
  sel.innerHTML = `<option value="">All topics</option>` + topics.map((t) => `<option ${t === cur ? "selected" : ""}>${esc(t)}</option>`).join("");
  renderPostList();
  if (state.selected) {
    const p = state.posts.find((x) => x.id === state.selected.id);
    if (p) { state.selected = p; renderPostMeta(); }
  }
}

function filteredPosts() {
  const q = $("#fSearch").value.trim().toLowerCase();
  const topic = $("#fTopic").value, min = +$("#fScore").value, undrafted = $("#fUndrafted").checked;
  const sort = $("#fSort").value;
  const list = state.posts.filter((p) =>
    (!topic || p.topic === topic) && p.score >= min && (!undrafted || !p.drafts.count) &&
    (!q || `${p.text} ${p.summary} @${p.username} ${p.display_name}`.toLowerCase().includes(q)));
  const key = { score: (p) => -p.score, likes: (p) => -p.likes, newest: (p) => -new Date(p.created_at), discovered: (p) => -new Date(p.discovered_at) }[sort];
  return list.sort((a, b) => key(a) - key(b));
}

function draftTags(d) {
  const tags = [];
  if (d.posted_kinds.length) tags.push(`<span class="pill posted">Posted</span>`);
  if (d.statuses.scheduled) tags.push(`<span class="pill scheduled">Scheduled</span>`);
  if (d.statuses.draft) tags.push(`<span class="pill draft">${d.statuses.draft === 1 ? "Draft" : d.statuses.draft + " drafts"}</span>`);
  if (d.statuses.failed) tags.push(`<span class="pill failed">Didn't post</span>`);
  return tags.join("");
}

function renderPostList() {
  const list = filteredPosts();
  $("#postList").innerHTML = list.length ? list.map((p) => `
    <div class="post-item ${state.selected?.id === p.id ? "active" : ""}" data-id="${esc(p.id)}">
      <div class="meta"><span class="score ${scoreCls(p.score)}" title="Crawler score">${p.score}</span><b>@${esc(p.username)}</b><span>${ago(p.created_at)}</span><span class="tag">${esc(p.topic)}</span></div>
      <div class="sum">${esc(p.summary || p.text)}</div>
      <div class="foot"><span class="metric" title="Likes">${I("heart-bold-duotone")}${num(p.likes)}</span><span class="metric" title="Replies">${I("chat-round-bold-duotone")}${num(p.replies)}</span>${draftTags(p.drafts)}</div>
    </div>`).join("") : (state.posts.length
      ? `<div class="empty compact">${I("filter-bold-duotone")}<div>No posts match these filters.</div></div>`
      : `<div class="empty compact">${I("inbox-bold-duotone")}<div>No posts yet.</div><button class="btn small primary" onclick="openCrawlDialog()">${I("magnifer-bold-duotone")}Find new posts</button></div>`);
}

["#fSearch", "#fTopic", "#fSort", "#fUndrafted"].forEach((s) => $(s).addEventListener("input", renderPostList));
$("#fScore").addEventListener("input", () => { $("#fScoreVal").textContent = $("#fScore").value; renderPostList(); });
$("#postList").addEventListener("click", (e) => {
  const item = e.target.closest(".post-item");
  if (!item) return;
  state.selected = state.posts.find((p) => p.id === item.dataset.id);
  $$(".post-item").forEach((el) => el.classList.toggle("active", el === item));
  renderPostDetail();
});

function renderPostDetail() {
  const p = state.selected;
  if (!p) return;
  $("#postDetail").innerHTML = `
    <div class="card">${sourceTweet(p)}</div>
    <div class="card panel" id="postMeta"></div>
    <div class="card panel">
      <h3><span class="num">B</span>Respond to this post</h3>
      <div class="kinds">${Object.entries(KINDS).filter(([, v]) => !v.own).map(([k, v]) => `<button class="kind-btn ${state.kind === k ? "active" : ""}" data-kind="${k}">${I(v.icon)}<div><b>${v.label}</b><span>${v.hint}</span></div></button>`).join("")}</div>
      <div class="gen-row">
        ${state.kind === "repost" ? "" : `<input id="genInstruction" placeholder="Anything Gemini should keep in mind? For example: mention the free trial">`}
        <button class="btn primary" id="genBtn">${I(state.kind === "repost" ? "repeat-bold-duotone" : "magic-stick-3-bold-duotone")}${state.kind === "repost" ? "Add repost to review" : "Write a draft"}</button>
      </div>
    </div>
    <div id="postDrafts" class="draft-grid"></div>`;
  renderPostMeta();
  $$(".kind-btn").forEach((b) => (b.onclick = () => { state.kind = b.dataset.kind; renderPostDetail(); }));
  $("#genBtn").onclick = (e) => busy(e.currentTarget, async () => {
    // Placeholder card while Gemini writes (removed when the drafts re-render).
    if (state.kind !== "repost") $("#postDrafts").insertAdjacentHTML("afterbegin", skeletonCard("Gemini is writing your " + KINDS[state.kind].noun));
    let d;
    try {
      d = await api("/api/drafts", { method: "POST", body: { post_id: p.id, kind: state.kind, instruction: $("#genInstruction")?.value || "" } });
    } finally { $("#postDrafts .skel-card")?.remove(); }
    toast(state.kind === "repost" ? "Repost added. Review it below." : "Your draft is ready below.");
    await Promise.all([loadDrafts(), loadPosts()]);
    document.querySelector(`#postDrafts [data-draft="${d.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  renderPostDrafts();
}

function renderPostMeta() {
  const p = state.selected, el = $("#postMeta");
  if (!p || !el) return;
  el.innerHTML = `<h3><span class="num">A</span>About this post</h3><dl class="insight">
    <dt>In short</dt><dd>${esc(p.summary)}</dd>
    <dt>Score</dt><dd><span class="score ${scoreCls(p.score)}">${p.score}</span> <span class="muted">${esc(p.reasons)}</span></dd>
    <dt>Found</dt><dd>${ago(p.discovered_at)} in <b>${esc(p.topic)}</b>, via ${esc(p.source)}</dd>
    <dt>Your drafts</dt><dd>${p.drafts.count ? draftTags(p.drafts) : `<span class="muted">None yet</span>`}</dd></dl>`;
}

function renderPostDrafts() {
  const el = $("#postDrafts");
  if (!el || !state.selected) return;
  el.innerHTML = state.drafts.filter((d) => d.post_id === state.selected.id).map(draftCard).join("");
  bindDraftCards(el);
}

// ------------------------------------------------------------------ drafts

async function loadDrafts({ background = false } = {}) {
  // Background refreshes must not wipe out what you're typing.
  if (background && document.activeElement?.closest(".draft")) return;
  const dirty = Object.fromEntries(state.drafts.filter((d) => d._dirty).map((d) => [d.id, d.text]));
  state.drafts = await api("/api/drafts");
  for (const d of state.drafts) {
    if (d.id in dirty && ["draft", "failed", "scheduled"].includes(d.status) && d.text !== dirty[d.id]) Object.assign(d, { text: dirty[d.id], _dirty: true });
  }
  renderLists();
  renderPostDrafts();
}

const isEditable = (d) => ["draft", "failed", "scheduled"].includes(d.status);

function previewBody(d) {
  const src = d.source;
  const textArea = isEditable(d)
    ? `<textarea class="body" rows="3" data-edit="${d.id}" placeholder="Write your ${KINDS[d.kind].noun}">${esc(d.text)}</textarea>`
    : `<div class="body">${linkify(d.text)}</div>`;
  if (d.kind === "repost") {
    return `<div class="repost-head">${I("repeat-bold-duotone")}${esc(handle())} will repost</div>${sourceTweet(src, { stats: false })}`;
  }
  if (d.kind === "reply") {
    return `<div class="thread">
      <div class="tweet">${avatar(src)}<div><div class="who"><b>${esc(src.display_name || src.username)}</b><span>@${esc(src.username)} · ${ago(src.created_at)}</span></div>
        <div class="body dim">${esc(src.text.slice(0, 220))}${src.text.length > 220 ? "..." : ""}</div></div></div>
      <div class="tweet">${ourAvatar()}<div><div class="who">${ourName()}</div><div class="replying">Replying to <a href="${esc(src.url)}" target="_blank" rel="noopener">@${esc(src.username)}</a></div>${textArea}</div></div>
    </div>`;
  }
  if (d.kind === "followup") {
    return `<div class="thread">
      <div class="tweet">${ourAvatar()}<div><div class="who">${ourName()}</div>
        <div class="body dim">${esc(src.text.slice(0, 220))}${src.text.length > 220 ? "..." : ""}</div></div></div>
      <div class="tweet">${ourAvatar()}<div><div class="who">${ourName()}</div><div class="replying">Under <a href="${esc(src.url)}" target="_blank" rel="noopener">your post</a></div>${textArea}</div></div>
    </div>`;
  }
  return `<div class="tweet">${ourAvatar()}<div><div class="who">${ourName()}</div>${textArea}${d.kind === "quote" ? quotedCard(src) : ""}</div></div>`;
}

// A follow-up whose post belongs to a different account than the one connected (e.g. a pasted link).
const notOurs = (d) => d.kind === "followup" && d.source.username && state.me && d.source.username.toLowerCase() !== state.me.toLowerCase();

function counter(d) {
  if (d.kind === "repost") return "";
  const n = xLength(d.text), lim = d.limit || 280, pct = Math.min(n / lim, 1);
  const r = 8, c = 2 * Math.PI * r, color = n > lim ? "var(--bad)" : n > lim - 20 ? "var(--warn)" : "var(--accent)";
  return `<span class="counter ${n > lim ? "over" : ""}" data-counter="${d.id}" title="Characters as X counts them">
    <svg class="ring" viewBox="0 0 20 20"><circle cx="10" cy="10" r="${r}" fill="none" stroke="var(--line)" stroke-width="2"/>
    <circle cx="10" cy="10" r="${r}" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="${c * pct} ${c}" transform="rotate(-90 10 10)"/></svg>
    ${n} / ${lim}</span>`;
}

function draftCard(d) {
  const k = KINDS[d.kind] || { label: d.kind, action: "Post now" };
  const fit = d.kind !== "repost" && d.kind !== "followup" && d.fit != null ? `<span class="fit ${fitCls(d.fit)}" title="How naturally this post connects to Deviantly, according to Gemini">Fit ${d.fit}/10</span>` : "";
  const rewrite = d.kind !== "repost" && d.status !== "scheduled"
    ? `<div class="rewrite"><input placeholder="Ask for changes, e.g. shorter, more playful" data-instr="${d.id}"><button class="btn small" data-act="regen">${I("magic-stick-3-bold-duotone")}Rewrite</button></div>` : "";
  let actions = "";
  if (d.status === "draft" || d.status === "failed") {
    actions = `<button class="btn small ghost danger" data-act="reject">Reject</button>
      <span class="spacer"></span>
      ${d.kind !== "repost" ? `<button class="btn small ghost" data-act="save">Save</button>` : ""}
      <button class="btn small" data-act="schedule">${I("calendar-mark-bold-duotone")}Schedule</button>
      <button class="btn small primary" data-act="approve">${I("plain-bold-duotone")}${k.action}</button>`;
  } else if (d.status === "scheduled") {
    actions = `<button class="btn small ghost danger" data-act="unschedule">Unschedule</button>
      <span class="spacer"></span>
      ${d.kind !== "repost" ? `<button class="btn small ghost" data-act="save">Save</button>` : ""}
      <button class="btn small" data-act="schedule">${I("clock-circle-bold-duotone")}Change time</button>
      <button class="btn small primary" data-act="now">${I("plain-bold-duotone")}${k.action}</button>`;
  } else if (d.status === "posted") {
    actions = `<span class="muted">Posted ${ago(d.updated_at)}</span><span class="spacer"></span>${d.kind !== "repost" && d.published_id ? `<button class="btn small" data-act="followup">${I(KINDS.followup.icon)}Write follow-up</button>` : ""}<a class="btn small" href="${esc(d.published_url)}" target="_blank" rel="noopener">View on X ${I("arrow-right-up-linear")}</a>`;
  } else if (d.status === "rejected") {
    actions = `<span class="spacer"></span><button class="btn small ghost danger" data-act="delete">Delete</button><button class="btn small" data-act="restore">${I("undo-left-round-bold-duotone")}Move back to review</button>`;
  } else if (d.status === "publishing") {
    actions = `<span class="pill publishing">Posting</span><span class="muted small">Sending it to X</span>`;
  }
  const sched = d.status === "scheduled" && d.scheduled_at
    ? `<div class="sched-banner">${I("calendar-mark-bold-duotone")}<b>${esc(when(d.scheduled_at))}</b><span class="muted">${until(d.scheduled_at)}</span></div>` : "";
  return `<article class="card draft ${d.status}" data-draft="${d.id}">
    <div class="draft-head"><span class="kind">${I(k.icon || "document-text-bold-duotone")}${esc(k.label)}</span>${statusPill(d.status)}${fit}
      <span class="spacer"></span>${counter(d)}<span class="muted">${ago(d.created_at)}</span></div>
    ${sched}
    ${previewBody(d)}
    ${d.angle && d.kind !== "repost" ? `<div class="angle"><b>The idea:</b> ${esc(d.angle)}</div>` : ""}
    ${notOurs(d) ? `<div class="callout warn">${I("danger-triangle-bold-duotone")}<span>This post is from @${esc(d.source.username)}, not your connected account ${esc(handle())}.</span></div>` : ""}
    ${d.error ? `<div class="callout bad err">${I("danger-triangle-bold-duotone")}<span>${esc(d.error)}</span></div>` : ""}
    ${rewrite}
    <div class="draft-actions">${actions}</div>
  </article>`;
}

function bindDraftCards(root) {
  $$("textarea[data-edit]", root).forEach((ta) => {
    const fit = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + 4 + "px"; };
    fit();
    ta.addEventListener("input", () => {
      fit();
      const d = state.drafts.find((x) => x.id === +ta.dataset.edit);
      d.text = ta.value;
      d._dirty = true;
      const c = $(`[data-counter="${d.id}"]`, ta.closest(".draft"));
      if (c) c.outerHTML = counter(d);
    });
  });
  $$("[data-act]", root).forEach((btn) => (btn.onclick = () => draftAction(btn)));
  $$("[data-instr]", root).forEach((inp) => inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); $('[data-act="regen"]', inp.closest(".draft")).click(); }
  }));
}

async function draftAction(btn) {
  const card = btn.closest(".draft"), id = +card.dataset.draft, act = btn.dataset.act;
  const d = state.drafts.find((x) => x.id === id);
  const text = $("textarea[data-edit]", card)?.value ?? d.text;
  let at = null;
  if (act === "followup") return openFollowup(d);
  if (act === "approve" || act === "now") { if (!(await confirmPublish(d, text))) return; }
  if (act === "schedule") { at = await pickTime(d); if (!at) return; }
  if (act === "delete" && !confirm("Delete this draft for good?")) return;
  await busy(btn, async () => {
    const post = (path, body) => api(`/api/drafts/${id}/${path}`, { method: "POST", body });
    if (act === "save") { await api(`/api/drafts/${id}`, { method: "PATCH", body: { text } }); toast("Saved"); }
    if (act === "regen") {
      card.classList.add("busy");
      try { await post("regenerate", { instruction: $(`[data-instr="${id}"]`, card)?.value || "" }); }
      finally { card.classList.remove("busy"); }
    }
    if (act === "reject") await post("reject");
    if (act === "restore") await post("restore");
    if (act === "unschedule") { await post("unschedule"); toast("Moved back to review"); }
    if (act === "delete") await api(`/api/drafts/${id}`, { method: "DELETE" });
    if (act === "schedule") {
      const res = await post("schedule", { at, text });
      toast(`Scheduled for ${when(res.scheduled_at)}`);
    }
    if (act === "approve" || act === "now") {
      if (act === "now") await post("unschedule");
      const res = await post("approve", { text });
      if (res.status === "posted") toast(`Your ${KINDS[d.kind].noun} is live on X`);
      else toast(`It didn't post: ${res.error}`, true);
    }
    d._dirty = false;  // the server has the latest text now
    await Promise.all([loadDrafts(), loadPosts()]);
    refreshStatus();
  });
}

function confirmPublish(d, text) {
  const what = { post: "this post", quote: "this quote post", reply: `this reply to @${esc(d.source.username)}`, repost: `@${esc(d.source.username)}'s post`, followup: "this comment under your post" }[d.kind];
  $("#confirmTitle").textContent = d.kind === "repost" ? "Repost this now?" : d.kind === "reply" ? "Send this reply now?" : d.kind === "followup" ? "Post this comment now?" : "Post this now?";
  $("#confirmOk").textContent = KINDS[d.kind].action;
  $("#confirmBody").innerHTML = `<p>${d.kind === "repost" ? "You'll repost" : "You'll publish"} ${what} from <b>${esc(handle())}</b> right away.</p>` +
    (d.kind !== "repost" ? `<div class="card quote-preview">${esc(text)}</div>` : "") +
    (!state.status?.x_mode ? `<div class="callout bad">No X account is connected yet. Add one on the X account tab first.</div>` : "");
  return dialogResult($("#confirmDialog"));
}

function dialogResult(dlg) {
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

// ------------------------------------------------------------------ scheduling

const pad = (n) => String(n).padStart(2, "0");
const toLocalInput = (dt) => `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;

function quickTimes() {
  const now = new Date(), out = [];
  const inHour = new Date(now.getTime() + 60 * 60000);
  inHour.setMinutes(Math.ceil(inHour.getMinutes() / 15) * 15, 0, 0);
  out.push(["In about an hour", inHour]);
  const tonight = new Date(now); tonight.setHours(18, 0, 0, 0);
  if (tonight - now > 30 * 60000) out.push(["This evening, 6:00 PM", tonight]);
  const tmMorning = new Date(now); tmMorning.setDate(now.getDate() + 1); tmMorning.setHours(9, 0, 0, 0);
  out.push(["Tomorrow, 9:00 AM", tmMorning]);
  const tmNoon = new Date(tmMorning); tmNoon.setHours(12, 30);
  out.push(["Tomorrow, 12:30 PM", tmNoon]);
  return out;
}

function pickTime(d) {
  const k = KINDS[d.kind];
  $("#scheduleTitle").textContent = d.status === "scheduled" ? "Pick a new time" : `Schedule this ${k.noun}`;
  $("#scheduleNote").textContent = d.kind === "reply"
    ? `The reply goes under @${d.source.username}'s post at the time you pick.`
    : d.kind === "followup" ? "The comment goes under your post at the time you pick, as long as the Studio is running."
    : "It goes out at the time you pick, as long as the Studio is running.";
  const input = $("#scheduleAt");
  const start = d.scheduled_at ? new Date(d.scheduled_at) : quickTimes()[0][1];
  input.value = toLocalInput(start);
  input.min = toLocalInput(new Date(Date.now() + 60000));
  $("#scheduleQuick").innerHTML = quickTimes().map(([label, t]) => `<button type="button" class="btn small" data-t="${toLocalInput(t)}">${label}</button>`).join("");
  $$("#scheduleQuick button").forEach((b) => (b.onclick = () => { input.value = b.dataset.t; }));
  $("#scheduleTz").textContent = `Times are in your timezone (${Intl.DateTimeFormat().resolvedOptions().timeZone}).`;
  return dialogResult($("#scheduleDialog")).then((ok) => (ok && input.value ? new Date(input.value).toISOString() : null));
}

// ------------------------------------------------------------------ lists

function renderLists() {
  const fill = (sel, list, empty) => {
    $(sel).innerHTML = list.length ? list.map(draftCard).join("") : emptyState(...empty);
    bindDraftCards($(sel));
  };
  fill("#reviewList", state.drafts.filter((d) => ["draft", "failed", "publishing"].includes(d.status)),
    ["checklist-minimalistic-bold-duotone", "Nothing to review right now. Write a draft from a post, or let Gemini write a few.",
     `<a class="btn small primary" href="#posts">${I("document-text-bold-duotone")}Browse posts</a>`]);
  fill("#scheduledList", state.drafts.filter((d) => d.status === "scheduled").sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at)),
    ["calendar-mark-bold-duotone", "Nothing scheduled. Click <b>Schedule</b> on any draft to pick a time.",
     `<a class="btn small" href="#review">${I("checklist-minimalistic-bold-duotone")}Go to review</a>`]);
  fill("#publishedList", state.drafts.filter((d) => d.status === "posted"), ["plain-bold-duotone", "Nothing published yet."]);
  fill("#rejectedList", state.drafts.filter((d) => d.status === "rejected"), ["close-circle-bold-duotone", "No rejected drafts."]);
}

function renderAutoStatus() {
  const a = state.status?.autodraft;
  $("#autoStatus").innerHTML = a?.running
    ? `<div class="callout info">${I("magic-stick-3-bold-duotone")}<span>Gemini is writing drafts, ${a.done} of ${a.total} done</span></div>
       <div class="draft-grid" style="margin-bottom:18px">${skeletonCard()}${a.total - a.done > 1 ? skeletonCard() : ""}</div>` : "";
}

$("#autoForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => {
    await api("/api/autodraft", { method: "POST", body: { count: +$("#aCount").value, kind: $("#aKind").value, min_score: +$("#aMin").value } });
    lastJobs.autodraft = true;
    drawerDismissed = false;
    await refreshStatus();
  });
});

// ------------------------------------------------------------------ follow-ups on your own posts

// Opens the follow-up dialog for a published draft, or (with no draft) for a pasted link to any of your posts.
async function openFollowup(d = null) {
  $("#fuUrlRow").hidden = !!d;
  $("#fuUrl").required = !d;
  $("#fuUrl").value = "";
  $("#fuInstruction").value = "";
  $("#fuPost").innerHTML = d ? `<div class="card quote-preview">${esc(d.text)}</div>` : "";
  if (!(await dialogResult($("#followDialog")))) return;
  const url = $("#fuUrl").value.trim();
  const who = url.match(/(?:x|twitter)\.com\/(\w{1,15})\/status/)?.[1];
  if (!d && who && state.me && who.toLowerCase() !== state.me.toLowerCase() &&
      !confirm(`This post is from @${who}, not your connected account @${state.me}. Write a follow-up for it anyway?`)) return;
  showTab("review");
  history.replaceState(null, "", "#review");
  $("#reviewList").insertAdjacentHTML("afterbegin", skeletonCard("Gemini is writing your follow-up comment"));
  let draft;
  try {
    draft = await api("/api/followups", { method: "POST", body: { draft_id: d?.id ?? null, url, instruction: $("#fuInstruction").value } });
  } catch (e) { toast(e.message, true); } finally { $("#reviewList .skel-card")?.remove(); }
  if (!draft) return;
  toast("Your follow-up comment is ready to review.");
  await loadDrafts();
  refreshStatus();
  document.querySelector(`#reviewList [data-draft="${draft.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("#followBtn").onclick = () => openFollowup();

// ------------------------------------------------------------------ autopilot

const OUTCOME = { running: "Running", posted: "Posted", queued: "Waiting for you", skipped: "Skipped", failed: "Didn't finish" };
const hourLabel = (h) => (h === 0 || h === 24 ? "midnight" : h === 12 ? "noon" : h < 12 ? `${h} AM` : `${h - 12} PM`);
let apData = null;

async function loadAutopilot() {
  apData = await api("/api/autopilot");
  if (!state.topics.length) state.topics = await api("/api/topics").catch(() => []);
  renderAutopilotForm();
  renderAutopilotStatus();
  renderRuns();
}

function renderAutopilotForm() {
  const st = apData.settings;
  $("#apEnabled").checked = st.enabled;
  $("#apEvery").value = String(st.every_hours);
  if (!$("#apEvery").value) $("#apEvery").value = "3";
  $("#apLimit").value = st.crawl_limit;
  $("#apSince").value = String(st.since_hours);
  if (!$("#apSince").value) $("#apSince").value = "24";
  const hours = (sel, from, to, v) => { $(sel).innerHTML = Array.from({ length: to - from + 1 }, (_, i) => from + i).map((h) => `<option value="${h}" ${h === v ? "selected" : ""}>${hourLabel(h)}</option>`).join(""); };
  hours("#apFrom", 0, 23, st.active_from);
  hours("#apTo", 1, 24, st.active_to);
  $$("#apKinds input").forEach((i) => (i.checked = st.kinds.includes(i.value)));
  $("#apTopics").innerHTML = state.topics.map((t) => `<label class="check"><input type="checkbox" value="${esc(t)}" ${st.topics.includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("");
  $$('input[name="apPublish"]').forEach((r) => (r.checked = r.value === st.publish));
  $("#apFit").innerHTML = Array.from({ length: 11 }, (_, i) => `<option value="${i}" ${i === st.min_fit ? "selected" : ""}>${i} out of 10</option>`).join("");
  $("#apCap").value = st.daily_cap;
}

function formSettings() {
  return {
    ...apData.settings,
    enabled: $("#apEnabled").checked,
    every_hours: +$("#apEvery").value,
    crawl_limit: +$("#apLimit").value || 10,
    since_hours: +$("#apSince").value,
    active_from: +$("#apFrom").value,
    active_to: +$("#apTo").value,
    kinds: $$("#apKinds input:checked").map((i) => i.value),
    topics: $$("#apTopics input:checked").map((i) => i.value),
    publish: $('input[name="apPublish"]:checked')?.value || "auto",
    min_fit: +$("#apFit").value,
    daily_cap: +$("#apCap").value || 4,
  };
}

async function saveAutopilot() {
  const body = formSettings();
  if (!body.kinds.length) throw new Error("Pick at least one thing the autopilot may write");
  const r = await api("/api/autopilot", { method: "PUT", body });
  apData.settings = r.settings;
  await refreshStatus();
  renderAutopilotStatus();
  return r;
}

function renderAutopilotStatus() {
  if (!apData || !state.status) return;
  const st = { ...apData.settings, ...{ enabled: state.status.autopilot.enabled } }, a = state.status.autopilot;
  const rows = [];
  if (a.running) {
    rows.push(`<div class="big"><span class="pill running">Running</span> Working on it now</div>`);
    rows.push(`<div class="muted">${esc(a.log[a.log.length - 1] || "Starting")}</div>`);
  } else if (st.enabled) {
    rows.push(`<div class="big"><span class="pill posted">On</span> ${a.next_run_at ? `Next run ${esc(when(a.next_run_at))}` : "Starting soon"}</div>`);
    if (!a.in_active_hours) rows.push(`<div class="muted">It's outside the hours you set (${hourLabel(st.active_from)} to ${hourLabel(st.active_to)}), so it'll wait until then.</div>`);
  } else {
    rows.push(`<div class="big"><span class="pill rejected">Off</span> Turn it on to start posting by itself</div>`);
  }
  rows.push(`<div class="muted">Posted ${a.posted_today} of ${a.daily_cap} today${st.publish === "review" ? ". Drafts wait for your approval" : ""}.</div>`);
  if (!state.status.x_mode) rows.push(`<div class="callout bad">Connect an X account first, or every draft will wait in To review.</div>`);
  if (!state.status.gemini) rows.push(`<div class="callout bad">Add GEMINI_API_KEY to .env so the autopilot can write.</div>`);
  $("#apStatus").innerHTML = rows.join("");
  $("#apRunNow").disabled = a.running || state.status.crawl.running;
  $("#apRunNote").textContent = st.publish === "auto" ? "Uses the settings on the right, so it may post right away." : "The draft will wait for you in To review.";
}

function renderRuns() {
  const runs = apData.runs;
  $("#apRuns").innerHTML = runs.length ? runs.map((r) => {
    const d = r.draft;
    const src = d?.source;
    return `<div class="card run">
      <div class="when"><span class="pill ${r.outcome}">${OUTCOME[r.outcome] || esc(r.outcome)}</span>${esc(when(r.started_at))}<span class="small">${r.trigger === "manual" ? "Started by you" : "On schedule"}</span></div>
      <div class="what">
        <div>${esc(r.message || "")}</div>
        ${r.found || r.considered ? `<div class="muted small">Found ${r.found} new posts, chose from ${r.considered}</div>` : ""}
        ${src ? `<div class="muted small">Chose a ${esc(KINDS[d.kind]?.noun || d.kind)} about <a href="${esc(src.url)}" target="_blank" rel="noopener">@${esc(src.username)}'s post</a>, fit ${d.fit}/10. ${esc(r.reason)}</div>` : ""}
        ${d?.text ? `<div class="text">${esc(d.text)}</div>` : ""}
        <div class="links">
          ${d?.status === "posted" ? `<a href="${esc(d.published_url)}" target="_blank" rel="noopener">View on X ${I("arrow-right-up-linear")}</a>` : ""}
          ${d && ["draft", "failed"].includes(d.status) ? `<a href="#review">Review it</a>` : ""}
        </div>
      </div></div>`;
  }).join("") : emptyState("routing-2-bold-duotone", "No runs yet. Turn the autopilot on, or click <b>Run once now</b>.");
}

$("#apEnabled").addEventListener("change", async (e) => {
  const on = e.target.checked;
  try {
    await saveAutopilot();
    toast(on ? "Autopilot is on. The first run starts in about a minute." : "Autopilot is off");
  } catch (err) { e.target.checked = !on; toast(err.message, true); }
});
$("#apForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => { await saveAutopilot(); $("#apSaved").textContent = "Saved"; setTimeout(() => ($("#apSaved").textContent = ""), 2500); });
});
$("#apRunNow").onclick = (e) => busy(e.currentTarget, async () => {
  await saveAutopilot();
  await api("/api/autopilot/run", { method: "POST" });
  drawerDismissed = false;
  lastJobs.autopilot = true;
  toast("Autopilot started. Follow along in the log.");
  await refreshStatus();
  renderAutopilotStatus();
});

// ------------------------------------------------------------------ topics

async function openTopics() {
  await renderTopicList();
  const dlg = $("#topicsDialog");
  if (!dlg.open) dlg.showModal();
  $("#tName").focus();
}

async function renderTopicList() {
  const list = await api("/api/topics/full");
  $("#topicList").innerHTML = list.map((t) => `
    <div class="topic">
      <div class="topic-main">
        <b>${esc(t.name)}</b>
        <div class="kw">${t.keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>
        ${t.exclude.length ? `<div class="muted small">Skips posts about ${esc(t.exclude.join(", "))}</div>` : ""}
        ${t.require.length ? `<div class="muted small">Must also mention ${esc(t.require.join(", "))}</div>` : ""}
      </div>
      <button class="btn small ghost danger" type="button" data-del-topic="${esc(t.name)}">Remove</button>
    </div>`).join("");
  $$("[data-del-topic]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Remove the topic "${b.dataset.delTopic}"? Posts already found stay.`)) return;
    await busy(b, async () => { await api(`/api/topics/${encodeURIComponent(b.dataset.delTopic)}`, { method: "DELETE" }); await topicsChanged(); });
  }));
}

async function topicsChanged() {
  state.topics = await api("/api/topics");
  await renderTopicList();
  // Refresh every topic picker that's on screen, keeping what's checked.
  const keep = (sel) => new Set($$(`${sel} input:checked`).map((i) => i.value));
  const cChecked = keep("#cTopics");
  $("#cTopics").innerHTML = state.topics.map((t) => `<label class="check"><input type="checkbox" value="${esc(t)}" ${cChecked.has(t) ? "checked" : ""}> ${esc(t)}</label>`).join("");
  if (apData) {
    const aChecked = keep("#apTopics");
    $("#apTopics").innerHTML = state.topics.map((t) => `<label class="check"><input type="checkbox" value="${esc(t)}" ${aChecked.has(t) ? "checked" : ""}> ${esc(t)}</label>`).join("");
  }
}

document.addEventListener("click", (e) => { if (e.target.closest("[data-open-topics]")) openTopics(); });
$("#topicsClose").onclick = () => $("#topicsDialog").close();
$("#topicForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => {
    const split = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    await api("/api/topics", { method: "POST", body: {
      name: $("#tName").value, keywords: split($("#tKeywords").value),
      exclude: split($("#tExclude").value), require: split($("#tRequire").value),
    } });
    toast(`Added "${$("#tName").value.trim()}". It's included the next time you find posts.`);
    $("#tName").value = ""; $("#tKeywords").value = ""; $("#tRequire").value = "";
    await topicsChanged();
  });
});

// ------------------------------------------------------------------ account

const FIELD_LABELS = {
  X_API_KEY: ["API key", "text"], X_API_SECRET: ["API key secret", "password"],
  X_ACCESS_TOKEN: ["Access token", "password"], X_ACCESS_TOKEN_SECRET: ["Access token secret", "password"],
  X_AUTH_TOKEN: ["auth_token cookie", "password"], X_CT0: ["ct0 cookie", "password"],
  X_USERNAME: ["Username, without the @", "text"], X_EMAIL: ["Email (optional, helps if X asks to confirm it's you)", "email"],
  X_PASSWORD: ["Password", "password"], X_TOTP_SECRET: ["Authenticator setup key (optional, only if you use 2FA)", "password"],
};
const MODE_HELP = {
  api: `<ol class="steps">
    <li>Sign in to <a href="https://developer.x.com" target="_blank" rel="noopener">developer.x.com</a> with the account you want to post from.</li>
    <li>Create a project and an app, or use the default app.</li>
    <li>In the app's <b>User authentication settings</b>, set permissions to <b>Read and write</b> and save. Do this first, or the tokens will only be able to read.</li>
    <li>Under <b>Keys and tokens</b>, copy the <b>API Key</b> and <b>API Key Secret</b>.</li>
    <li>On the same page, generate an <b>Access Token and Secret</b> and copy both.</li>
    <li>Paste all four below, then click <b>Save and test</b>.</li></ol>`,
  cookies: `<ol class="steps">
    <li>In Chrome, sign in to <a href="https://x.com" target="_blank" rel="noopener">x.com</a> with the account you want to post from.</li>
    <li>Open DevTools (Option+Cmd+I on a Mac, F12 on Windows), then go to <b>Application</b>, <b>Cookies</b>, <code>https://x.com</code>.</li>
    <li>Copy the values of <code>auth_token</code> and <code>ct0</code> into the fields below.</li>
    <li>Click <b>Save and test</b>. If you sign out of that browser, you'll need to copy them again.</li></ol>`,
  password: `<ol class="steps">
    <li>Enter the username, email and password of the account you want to post from.</li>
    <li>If you use an authenticator app for 2FA, paste the <b>setup key</b> (the text code shown when you turned on 2FA), not a 6-digit code.</li>
    <li>Click <b>Save and test</b>. The Studio signs in once and remembers the session.</li>
    <li>If X asks you to confirm it's you or blocks the sign-in, use browser cookies instead.</li></ol>`,
};

async function loadAccount() {
  state.account = await api("/api/account");
  const mode = state.account.mode || $('input[name="mode"]:checked')?.value || "api";
  $$('input[name="mode"]').forEach((r) => (r.checked = r.value === mode));
  renderAccountForm(mode);
}

function renderAccountForm(mode) {
  const fields = state.account.fields[mode];
  $("#twikitWarn").hidden = mode === "api";
  $("#accountForm").innerHTML = MODE_HELP[mode] +
    Object.entries(fields).map(([k, f]) => {
      const [label, type] = FIELD_LABELS[k];
      const saved = f.set ? `<span class="saved">Saved</span>` : "";
      return `<label>${label} ${saved}<input name="${k}" type="${type}" autocomplete="off" value="${esc(f.value || "")}" placeholder="${f.set && type === "password" ? "Leave blank to keep the saved value" : ""}"></label>`;
    }).join("") +
    `<div class="actions">
      <button class="btn ghost danger" type="button" id="accDisconnect">Disconnect</button>
      <span class="spacer"></span>
      <button class="btn" type="button" id="accVerify">Test connection</button>
      <button class="btn primary" type="submit">Save and test</button></div>`;
  $("#accVerify").onclick = (e) => busy(e.currentTarget, verifyAccount);
  $("#accDisconnect").onclick = (e) => {
    if (!confirm("Remove the saved X sign-in details from .env?")) return;
    busy(e.currentTarget, async () => {
      state.account = await api("/api/account/disconnect", { method: "POST" });
      state.me = null;
      renderAccountForm(mode);
      refreshStatus();
      $("#accountResult").innerHTML = `<div class="callout info">${I("check-circle-bold-duotone")}<span>Disconnected. Your sign-in details were removed.</span></div>`;
    });
  };
}

$("#modes").addEventListener("change", (e) => renderAccountForm(e.target.value));
$("#accountForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const mode = $('input[name="mode"]:checked').value;
  const values = Object.fromEntries($$("#accountForm input").map((i) => [i.name, i.value]));
  await busy(e.submitter, async () => {
    state.account = await api("/api/account", { method: "POST", body: { mode, values } });
    renderAccountForm(mode);
    await verifyAccount();
    refreshStatus();
  });
});

async function verifyAccount() {
  const r = await api("/api/account/verify", { method: "POST" });
  state.me = r.ok ? r.username : null;
  $("#accountResult").innerHTML = r.ok
    ? `<div class="callout good">${I("check-circle-bold-duotone")}<span>You're signed in as <b>@${esc(r.username)}</b>. Posts will go out from this account.</span></div>`
    : `<div class="callout bad">${I("danger-triangle-bold-duotone")}<span><b>Couldn't sign in.</b> ${esc(r.error)}</span></div>`;
  refreshStatus();
  renderLists();
}

// ------------------------------------------------------------------ brand

async function loadBrand() {
  const [brand, persona] = await Promise.all([api("/api/brand"), api("/api/persona").catch(() => null)]);
  $("#brandText").value = brand.text;
  if (persona) $("#personaText").value = persona.text;
  else $("#personaMsg").textContent = "Restart the Studio to load the persona.";
}
$("#personaSave").onclick = (e) => busy(e.currentTarget, async () => {
  try {
    await api("/api/persona", { method: "PUT", body: { text: $("#personaText").value } });
  } catch (err) {
    // A 404 here means the running server predates the persona feature.
    throw new Error(err.message === "Something went wrong (404)" || err.message === "Not Found"
      ? "Couldn't save: the Studio needs a restart to save the persona (Ctrl+C, then python3 studio.py)" : err.message);
  }
  $("#personaMsg").textContent = "Saved. New drafts will use this voice.";
});
$("#brandSave").onclick = (e) => busy(e.currentTarget, async () => {
  await api("/api/brand", { method: "PUT", body: { text: $("#brandText").value } });
  $("#brandMsg").textContent = "Saved. New drafts will use it.";
});

// ------------------------------------------------------------------ tabs and startup

function showTab(name) {
  const b = $(`#tabs button[data-tab="${name}"]`);
  if (!b) return;
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  const tab = $(`#tab-${name}`);
  tab.classList.add("entering");  // staggered cascade only on entry, not on every refresh
  clearTimeout(tab._enterTimer);
  tab._enterTimer = setTimeout(() => tab.classList.remove("entering"), 1100);
  if (name === "autopilot" && apData) { renderAutopilotStatus(); loadAutopilot().catch(() => {}); }
}
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  showTab(b.dataset.tab);
  history.replaceState(null, "", `#${b.dataset.tab}`);
});
// Flashlight hover: cards light up around the cursor (background and border).
document.addEventListener("pointermove", (e) => {
  const card = e.target.closest?.(".card");
  if (!card) return;
  const r = card.getBoundingClientRect();
  card.style.setProperty("--mx", `${e.clientX - r.left}px`);
  card.style.setProperty("--my", `${e.clientY - r.top}px`);
}, { passive: true });

window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

(async function boot() {
  showTab(location.hash.slice(1) || "posts");
  $("#postDetail").innerHTML = emptyState("document-text-bold-duotone", "Pick a post on the left to read it and write a post, quote, reply or repost about it.");
  $("#postList").innerHTML = Array.from({ length: 5 }, () => `<div class="post-item"><div class="skeleton sk-line w40" style="height:10px;margin-bottom:10px"></div><div class="skeleton sk-line w90" style="height:10px;margin-bottom:8px"></div><div class="skeleton sk-line w60" style="height:10px"></div></div>`).join("");
  await Promise.all([loadPosts(), loadDrafts(), loadAccount(), loadBrand(), loadAutopilot()]).catch((e) => toast(e.message, true));
  poll();
  // Learn the @handle for previews if an account is set up (quietly ignore failures).
  if (state.account?.mode) {
    api("/api/account/verify", { method: "POST" }).then((r) => {
      if (r.ok) { state.me = r.username; refreshStatus(); renderLists(); renderPostDrafts(); }
    }).catch(() => {});
  }
})();
