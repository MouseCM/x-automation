// Deviantly X Studio front end: plain JS, talks to the FastAPI backend under /api.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  posts: [], selected: null, kind: "quote", status: null, account: null, me: null,
  drafts: [], topics: [],
};

const KINDS = {
  post: { label: "Post", hint: "Original post about the news + Deviantly" },
  quote: { label: "Quote post", hint: "Repost with your commentary on top" },
  reply: { label: "Reply", hint: "Comment under their post" },
  repost: { label: "Repost", hint: "Plain repost, no text" },
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
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

function toast(msg, bad = false) {
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 7000 : 3500);
}

async function busy(btn, fn) {
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> ${old}`;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.innerHTML = old; }
}

// ------------------------------------------------------------------ formatting

const LINK_RE = /https?:\/\/\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:ai|com|io|co|net|org|app|dev|so|gg|xyz)\b(?:\/\S*)?/gi;
function xLength(text) {  // mirrors drafter.x_length: links count 23, wide chars count 2
  let n = 0;
  const links = text.match(LINK_RE) || [];
  for (const part of text.split(LINK_RE)) for (const ch of part) n += ch.codePointAt(0) > 0x10ff ? 2 : 1;
  return n + 23 * links.length;
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
const num = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K" : String(n ?? 0));
const scoreCls = (s) => (s >= 65 ? "hi" : s >= 50 ? "mid" : "lo");
const fitCls = (f) => (f >= 7 ? "hi" : f >= 4 ? "mid" : "lo");
const linkify = (text) => esc(text).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
const initials = (name) => (name || "?").trim().charAt(0).toUpperCase();
const hue = (s) => [...(s || "")].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);

function avatar(p) {
  return `<div class="avatar" style="background:hsl(${hue(p.username)} 45% 48%)">${esc(initials(p.display_name || p.username))}</div>`;
}
function ourAvatar() { return `<div class="avatar us">D</div>`; }
function ourName() {
  const h = state.me ? `@${state.me}` : "@your_account";
  return `<b>Deviantly</b><span>${esc(h)} · now</span>`;
}

function sourceTweet(p, { stats = true } = {}) {
  return `<div class="tweet">
    ${avatar(p)}
    <div>
      <div class="who"><b>${esc(p.display_name || p.username)}</b><span>@${esc(p.username)} · ${ago(p.created_at)}</span></div>
      <div class="body">${linkify(p.text)}</div>
      ${stats ? `<div class="stats"><span>💬 ${num(p.replies)}</span><span>♥ ${num(p.likes)}</span><a href="${esc(p.url)}" target="_blank" rel="noopener">Open on X ↗</a></div>` : ""}
    </div></div>`;
}

function quotedCard(p) {
  return `<div class="quoted"><div class="tweet"><div>
    <div class="who"><b>${esc(p.display_name || p.username)}</b><span>@${esc(p.username)} · ${ago(p.created_at)}</span></div>
    <div class="body">${linkify(p.text)}</div></div></div></div>`;
}

// ------------------------------------------------------------------ status bar

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
  } catch { return; }
  const s = state.status, c = s.counts || {};
  const chips = [];
  chips.push(s.gemini ? `<span class="chip ok">Gemini</span>` : `<span class="chip bad" title="Add GEMINI_API_KEY to .env">Gemini key missing</span>`);
  if (!s.x_mode) chips.push(`<span class="chip bad">X not connected</span>`);
  else chips.push(`<span class="chip ${state.me ? "ok" : ""}">X ${state.me ? "@" + esc(state.me) : `(${esc(s.x_mode)})`}</span>`);
  if (s.crawl.running) chips.push(`<span class="chip busy">Crawling…</span>`);
  if (s.autodraft.running) chips.push(`<span class="chip busy">Drafting ${s.autodraft.done}/${s.autodraft.total}</span>`);
  $("#chips").innerHTML = chips.join("");
  $("#nReview").textContent = (c.draft || 0) + (c.failed || 0) || "";
  $("#nPublished").textContent = c.posted || "";
  $("#crawlBtn").disabled = s.crawl.running;
  $("#crawlBtn").textContent = s.crawl.running ? "Crawling…" : "Run crawler";
  renderDrawer();
  renderAutoStatus();
  return s;
}

let lastJobs = { crawl: false, autodraft: false };
async function poll() {
  const s = await refreshStatus();
  if (s) {
    // When a job finishes, reload the data it changed.
    if (lastJobs.crawl && !s.crawl.running) {
      toast(s.crawl.ok ? `Crawl finished: ${s.crawl.message}` : `Crawl failed: ${s.crawl.message}`, !s.crawl.ok);
      await loadPosts();
    }
    if (lastJobs.autodraft && !s.autodraft.running) {
      toast(`Auto-draft: ${s.autodraft.message}`, s.autodraft.ok === false);
      await Promise.all([loadPosts(), loadDrafts()]);
    } else if (s.autodraft.running) {
      loadDrafts({ background: true });
    }
    lastJobs = { crawl: s.crawl.running, autodraft: s.autodraft.running };
  }
  setTimeout(poll, s && (s.crawl.running || s.autodraft.running) ? 1500 : 8000);
}

// ------------------------------------------------------------------ crawler drawer

let drawerDismissed = false;
function renderDrawer() {
  const s = state.status;
  if (!s) return;
  const job = s.autodraft.running || (!s.crawl.running && s.autodraft.log.length && s.autodraft.started_at > (s.crawl.started_at || "")) ? s.autodraft : s.crawl;
  const show = (s.crawl.running || s.autodraft.running || job.log.length) && !drawerDismissed;
  $("#drawer").hidden = !show;
  if (!show) return;
  $("#drawerTitle").textContent = job === s.crawl ? "Crawler" : "Auto-draft";
  $("#drawerMsg").textContent = job.running ? "running…" : job.message || "";
  const pre = $("#drawerLog");
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = job.log.join("\n");
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}
$("#drawerClose").onclick = () => { drawerDismissed = true; $("#drawer").hidden = true; };

async function openCrawlDialog() {
  if (!state.topics.length) state.topics = await api("/api/topics").catch(() => []);
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
    engines: $("#cEngines").value,
    autodraft: $("#cAuto").checked ? { count: +$("#cACount").value, kind: $("#cAKind").value, min_score: +$("#cAMin").value } : null,
  };
  try {
    await api("/api/crawl", { method: "POST", body });
    drawerDismissed = false;
    lastJobs.crawl = true;
    toast("Crawler started");
    refreshStatus();
  } catch (err) { toast(err.message, true); }
});

// ------------------------------------------------------------------ crawled posts

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
  let list = state.posts.filter((p) =>
    (!topic || p.topic === topic) && p.score >= min && (!undrafted || !p.drafts.count) &&
    (!q || `${p.text} ${p.summary} @${p.username} ${p.display_name}`.toLowerCase().includes(q)));
  const key = { score: (p) => -p.score, likes: (p) => -p.likes, newest: (p) => -new Date(p.created_at), discovered: (p) => -new Date(p.discovered_at) }[sort];
  return list.sort((a, b) => key(a) - key(b));
}

function draftTags(d) {
  const tags = [];
  if (d.posted_kinds.length) tags.push(`<span class="tag posted">posted ${esc(d.posted_kinds.join(", "))}</span>`);
  if (d.statuses.draft) tags.push(`<span class="tag draft">${d.statuses.draft} to review</span>`);
  if (d.statuses.failed) tags.push(`<span class="tag failed">${d.statuses.failed} failed</span>`);
  return tags.join("");
}

function renderPostList() {
  const list = filteredPosts();
  $("#postList").innerHTML = list.length ? list.map((p) => `
    <div class="post-item ${state.selected?.id === p.id ? "active" : ""}" data-id="${esc(p.id)}">
      <div class="meta"><span class="score ${scoreCls(p.score)}">${p.score}</span><b>@${esc(p.username)}</b>· ${ago(p.created_at)}<span class="tag">${esc(p.topic)}</span></div>
      <div class="sum">${esc(p.summary || p.text)}</div>
      <div class="foot"><span>♥ ${num(p.likes)}</span><span>💬 ${num(p.replies)}</span>${draftTags(p.drafts)}</div>
    </div>`).join("") : `<div class="empty">${state.posts.length ? "No posts match these filters." : "No crawled posts yet. Click <b>Run crawler</b>."}</div>`;
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
      <h3>Create a draft</h3>
      <div class="kinds">${Object.entries(KINDS).map(([k, v]) => `<button class="kind-btn ${state.kind === k ? "active" : ""}" data-kind="${k}"><b>${v.label}</b><span>${v.hint}</span></button>`).join("")}</div>
      <div class="gen-row">
        <input id="genInstruction" placeholder="Optional instruction, e.g. “mention the free trial”, “ask a question”">
        <button class="btn primary" id="genBtn">${state.kind === "repost" ? "Queue repost" : "Generate with Gemini"}</button>
      </div>
    </div>
    <div id="postDrafts" class="draft-grid"></div>`;
  renderPostMeta();
  $$(".kind-btn").forEach((b) => (b.onclick = () => { state.kind = b.dataset.kind; renderPostDetail(); }));
  $("#genBtn").onclick = (e) => busy(e.currentTarget, async () => {
    const d = await api("/api/drafts", { method: "POST", body: { post_id: p.id, kind: state.kind, instruction: $("#genInstruction").value } });
    toast(state.kind === "repost" ? "Repost queued for approval" : "Draft ready for review");
    await Promise.all([loadDrafts(), loadPosts()]);
    document.querySelector(`[data-draft="${d.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  renderPostDrafts();
}

function renderPostMeta() {
  const p = state.selected, el = $("#postMeta");
  if (!p || !el) return;
  el.innerHTML = `<h3>Crawler insight</h3><dl class="insight">
    <dt>Summary</dt><dd>${esc(p.summary)}</dd>
    <dt>Score</dt><dd><span class="score ${scoreCls(p.score)}">${p.score}</span> ${esc(p.reasons)}</dd>
    <dt>Topic</dt><dd>${esc(p.topic)} · found via ${esc(p.source)} · crawled ${ago(p.discovered_at)} ago</dd>
    <dt>Drafts</dt><dd>${p.drafts.count ? draftTags(p.drafts) : "none yet"}</dd></dl>`;
}

function renderPostDrafts() {
  const el = $("#postDrafts");
  if (!el || !state.selected) return;
  const mine = state.drafts.filter((d) => d.post_id === state.selected.id);
  el.innerHTML = mine.map(draftCard).join("");
  bindDraftCards(el);
}

// ------------------------------------------------------------------ drafts

async function loadDrafts({ background = false } = {}) {
  // Background refreshes (while auto-drafting) must not wipe out what you're typing.
  if (background && document.activeElement?.closest(".draft")) return;
  const dirty = Object.fromEntries(state.drafts.filter((d) => d._dirty).map((d) => [d.id, d.text]));
  state.drafts = await api("/api/drafts");
  for (const d of state.drafts) {
    if (d.id in dirty && ["draft", "failed"].includes(d.status) && d.text !== dirty[d.id]) Object.assign(d, { text: dirty[d.id], _dirty: true });
  }
  renderReview();
  renderPostDrafts();
}

function previewBody(d) {
  const src = d.source, editable = ["draft", "failed"].includes(d.status);
  const textArea = editable
    ? `<textarea class="body" rows="4" data-edit="${d.id}">${esc(d.text)}</textarea>`
    : `<div class="body">${linkify(d.text)}</div>`;
  if (d.kind === "repost") {
    return `<div class="repost-head">🔁 ${state.me ? "@" + esc(state.me) : "You"} reposted</div>${sourceTweet(src, { stats: false })}`;
  }
  if (d.kind === "reply") {
    return `<div class="thread">
      <div class="tweet">${avatar(src)}<div><div class="who"><b>${esc(src.display_name || src.username)}</b><span>@${esc(src.username)} · ${ago(src.created_at)}</span></div>
        <div class="body" style="font-size:14px;color:var(--muted)">${esc(src.text.slice(0, 220))}${src.text.length > 220 ? "…" : ""}</div></div></div>
      <div class="tweet">${ourAvatar()}<div><div class="who">${ourName()}</div><div class="replying">Replying to <a href="${esc(src.url)}" target="_blank" rel="noopener">@${esc(src.username)}</a></div>${textArea}</div></div>
    </div>`;
  }
  return `<div class="tweet">${ourAvatar()}<div><div class="who">${ourName()}</div>${textArea}${d.kind === "quote" ? quotedCard(src) : ""}</div></div>`;
}

function counter(d) {
  if (d.kind === "repost") return "";
  const n = xLength(d.text), lim = d.limit || 280, pct = Math.min(n / lim, 1);
  const r = 8, c = 2 * Math.PI * r, color = n > lim ? "var(--bad)" : n > lim - 20 ? "var(--warn)" : "var(--x-blue)";
  return `<span class="counter ${n > lim ? "over" : ""}" data-counter="${d.id}">
    <svg class="ring" viewBox="0 0 20 20"><circle cx="10" cy="10" r="${r}" fill="none" stroke="var(--line)" stroke-width="2"/>
    <circle cx="10" cy="10" r="${r}" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="${c * pct} ${c}" transform="rotate(-90 10 10)"/></svg>
    ${n}/${lim}</span>`;
}

function draftCard(d) {
  const editable = ["draft", "failed"].includes(d.status);
  const fit = d.kind !== "repost" && d.fit != null ? `<span class="fit ${fitCls(d.fit)}" title="Gemini's rating of how well this post fits Deviantly">fit ${d.fit}/10</span>` : "";
  let actions = "";
  if (editable) {
    actions = `
      ${d.kind !== "repost" ? `<input placeholder="Tell Gemini what to change…" data-instr="${d.id}"><button class="btn small" data-act="regen">↻ Regenerate</button><button class="btn small" data-act="save">Save</button>` : ""}
      <button class="btn small danger" data-act="reject">Reject</button>
      <button class="btn small primary" data-act="approve">${d.kind === "repost" ? "Approve & repost" : "Approve & publish"}</button>`;
  } else if (d.status === "posted") {
    actions = `<a class="btn small" href="${esc(d.published_url)}" target="_blank" rel="noopener">View on X ↗</a><span class="muted">${ago(d.updated_at)} ago</span>`;
  } else if (d.status === "rejected") {
    actions = `<button class="btn small" data-act="restore">Restore</button><button class="btn small danger" data-act="delete">Delete</button>`;
  } else if (d.status === "publishing") {
    actions = `<span class="muted"><span class="spinner"></span> publishing…</span>`;
  }
  return `<article class="card draft ${d.status}" data-draft="${d.id}">
    <div class="draft-head"><span class="kind">${esc(KINDS[d.kind]?.label || d.kind)}</span>
      <span class="tag ${d.status === "posted" ? "posted" : d.status === "failed" ? "failed" : d.status === "draft" ? "draft" : ""}">${esc(d.status)}</span>
      ${fit}<span class="spacer"></span>${counter(d)}<span class="muted">${ago(d.created_at)}</span></div>
    ${previewBody(d)}
    ${d.angle && d.kind !== "repost" ? `<div class="angle"><b>Angle:</b> ${esc(d.angle)}</div>` : ""}
    ${d.error ? `<div class="callout bad err">${esc(d.error)}</div>` : ""}
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
}

async function draftAction(btn) {
  const card = btn.closest(".draft"), id = +card.dataset.draft, act = btn.dataset.act;
  const d = state.drafts.find((x) => x.id === id);
  const text = $("textarea[data-edit]", card)?.value ?? d.text;
  if (act === "approve") {
    if (!(await confirmPublish(d, text))) return;
  }
  if (act === "delete" && !confirm("Delete this draft permanently?")) return;
  await busy(btn, async () => {
    let res;
    if (act === "save") res = await api(`/api/drafts/${id}`, { method: "PATCH", body: { text } });
    if (act === "regen") {
      card.classList.add("busy");
      try { res = await api(`/api/drafts/${id}/regenerate`, { method: "POST", body: { instruction: $(`[data-instr="${id}"]`, card)?.value || "" } }); }
      finally { card.classList.remove("busy"); }
    }
    if (act === "reject") res = await api(`/api/drafts/${id}/reject`, { method: "POST" });
    if (act === "restore") res = await api(`/api/drafts/${id}/restore`, { method: "POST" });
    if (act === "delete") await api(`/api/drafts/${id}`, { method: "DELETE" });
    if (act === "approve") {
      res = await api(`/api/drafts/${id}/approve`, { method: "POST", body: { text } });
      if (res.status === "posted") toast("Published to X ✓");
      else toast(`Publishing failed: ${res.error}`, true);
    }
    if (act === "save") toast("Saved");
    d._dirty = false;  // the server now has the latest text for this draft
    await Promise.all([loadDrafts(), loadPosts()]);
    refreshStatus();
  });
}

function confirmPublish(d, text) {
  const who = state.me ? `@${esc(state.me)}` : `your ${esc(state.status?.x_mode || "")} account`;
  const what = { post: "a new post", quote: "a quote post", reply: `a reply to @${esc(d.source.username)}`, repost: `a repost of @${esc(d.source.username)}` }[d.kind];
  $("#confirmTitle").textContent = d.kind === "repost" ? "Repost on X?" : "Publish to X?";
  $("#confirmBody").innerHTML = `<p>This publishes ${what} as <b>${who}</b> right now.</p>` +
    (d.kind !== "repost" ? `<div class="card" style="padding:12px 14px;white-space:pre-wrap">${esc(text)}</div>` : "") +
    (!state.status?.x_mode ? `<div class="callout bad">No X account is connected yet. Set one up on the X account tab.</div>` : "");
  const dlg = $("#confirmDialog");
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

function renderReview() {
  const queue = state.drafts.filter((d) => ["draft", "failed", "publishing"].includes(d.status));
  $("#reviewList").innerHTML = queue.length ? queue.map(draftCard).join("")
    : `<div class="empty">Nothing to review. Generate drafts from the <b>Crawled posts</b> tab, or use <b>Auto-draft</b>.</div>`;
  bindDraftCards($("#reviewList"));
  const posted = state.drafts.filter((d) => d.status === "posted");
  $("#publishedList").innerHTML = posted.length ? posted.map(draftCard).join("") : `<div class="empty">Nothing published yet.</div>`;
  const rejected = state.drafts.filter((d) => d.status === "rejected");
  $("#rejectedList").innerHTML = rejected.length ? rejected.map(draftCard).join("") : `<div class="empty">No rejected drafts.</div>`;
  bindDraftCards($("#rejectedList"));
}

function renderAutoStatus() {
  const a = state.status?.autodraft;
  $("#autoStatus").innerHTML = a?.running
    ? `<div class="callout good"><span class="spinner"></span> Gemini is drafting ${a.done}/${a.total}…</div>` : "";
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

// ------------------------------------------------------------------ account

const FIELD_LABELS = {
  X_API_KEY: ["API key (consumer key)", "text"], X_API_SECRET: ["API key secret", "password"],
  X_ACCESS_TOKEN: ["Access token", "password"], X_ACCESS_TOKEN_SECRET: ["Access token secret", "password"],
  X_AUTH_TOKEN: ["auth_token cookie", "password"], X_CT0: ["ct0 cookie", "password"],
  X_USERNAME: ["Username (without @)", "text"], X_EMAIL: ["Email (optional, helps login checks)", "email"],
  X_PASSWORD: ["Password", "password"], X_TOTP_SECRET: ["2FA TOTP secret (optional)", "password"],
};
const MODE_HELP = {
  api: `<ol class="steps">
    <li>Sign in to <a href="https://developer.x.com" target="_blank" rel="noopener">developer.x.com</a> with the account that will post, and open the Developer Portal.</li>
    <li>Create a Project and an App (or use the default one).</li>
    <li>In the app's <b>User authentication settings</b>, set permissions to <b>Read and write</b> and save. Do this <i>before</i> step 5, or the tokens will be read-only.</li>
    <li>Under <b>Keys and tokens</b>, copy the <b>API Key</b> and <b>API Key Secret</b> (consumer keys).</li>
    <li>On the same page, generate the <b>Access Token and Secret</b> and copy both.</li>
    <li>Paste all four below, click <b>Save</b>, then <b>Test connection</b>.</li></ol>`,
  cookies: `<ol class="steps">
    <li>In Chrome, log in to <a href="https://x.com" target="_blank" rel="noopener">x.com</a> with the account that will post.</li>
    <li>Open DevTools (⌥⌘I on Mac, F12 on Windows) → <b>Application</b> → <b>Cookies</b> → <code>https://x.com</code>.</li>
    <li>Copy the value of <code>auth_token</code> and of <code>ct0</code> into the fields below.</li>
    <li>Click <b>Save</b>, then <b>Test connection</b>. Logging out of that browser invalidates the cookies.</li></ol>`,
  password: `<ol class="steps">
    <li>Enter the username (without @), email and password of the account that will post.</li>
    <li>If the account has 2FA with an authenticator app, paste the <b>TOTP secret</b> (the text key shown when you set up 2FA), not a 6-digit code.</li>
    <li>Click <b>Save</b>, then <b>Test connection</b>. It logs in once and saves the session to <code>workflow/.x_session.json</code>.</li>
    <li>If X asks for a confirmation or blocks the login, use cookies mode instead.</li></ol>`,
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
      const saved = f.set ? `<span class="saved">✓ saved</span>` : "";
      return `<label>${label} ${saved}<input name="${k}" type="${type}" autocomplete="off" value="${esc(f.value || "")}" placeholder="${f.set && type === "password" ? "•••••••• (leave blank to keep)" : ""}"></label>`;
    }).join("") +
    `<div class="actions">
      <button class="btn danger" type="button" id="accDisconnect">Disconnect</button>
      <button class="btn" type="button" id="accVerify">Test connection</button>
      <button class="btn primary" type="submit">Save</button></div>`;
  $("#accVerify").onclick = (e) => busy(e.currentTarget, verifyAccount);
  $("#accDisconnect").onclick = (e) => {
    if (!confirm("Remove all saved X credentials from .env?")) return;
    busy(e.currentTarget, async () => { state.account = await api("/api/account/disconnect", { method: "POST" }); state.me = null; renderAccountForm(mode); refreshStatus(); $("#accountResult").innerHTML = ""; });
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
    toast("Saved to .env");
    await verifyAccount();
    refreshStatus();
  });
});

async function verifyAccount() {
  const r = await api("/api/account/verify", { method: "POST" });
  state.me = r.ok ? r.username : null;
  $("#accountResult").innerHTML = r.ok
    ? `<div class="callout good">Connected as <b>@${esc(r.username)}</b> via ${esc(r.mode)} mode.</div>`
    : `<div class="callout bad">${esc(r.error)}</div>`;
  refreshStatus();
  renderReview();
}

// ------------------------------------------------------------------ brand

async function loadBrand() { $("#brandText").value = (await api("/api/brand")).text; }
$("#brandSave").onclick = (e) => busy(e.currentTarget, async () => {
  await api("/api/brand", { method: "PUT", body: { text: $("#brandText").value } });
  $("#brandMsg").textContent = "Saved. New drafts will use it.";
});

// ------------------------------------------------------------------ tabs + boot

$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${b.dataset.tab}`));
  history.replaceState(null, "", `#${b.dataset.tab}`);
});

window.addEventListener("hashchange", () => $(`#tabs button[data-tab="${location.hash.slice(1)}"]`)?.click());

(async function boot() {
  const tab = location.hash.slice(1);
  if (tab) $(`#tabs button[data-tab="${tab}"]`)?.click();
  await Promise.all([loadPosts(), loadDrafts(), loadAccount(), loadBrand()]).catch((e) => toast(e.message, true));
  poll();
  // Learn the @handle for previews if an account is configured (silent if it fails).
  if (state.account?.mode) api("/api/account/verify", { method: "POST" }).then((r) => { if (r.ok) { state.me = r.username; refreshStatus(); renderReview(); renderPostDrafts(); } }).catch(() => {});
})();
