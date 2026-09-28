// xrpl_agent_id dashboard — vanilla JS, no deps
// Polls /api/* endpoints every 2s and updates the DOM.

const REFRESH_MS = 2000;
const BITHOMP_TEST = (hash) =>
  `https://test.bithomp.com/explorer/${hash}`;

const $ = (id) => document.getElementById(id);
const fmtTime = (epoch) => {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toISOString().slice(11, 19) + "Z";
};
const shortHash = (h) => (h ? h.slice(0, 10) + "…" : "—");
const shortAddr = (a) => (a ? a.slice(0, 6) + "…" + a.slice(-4) : "—");
const shortType = (t) => {
  if (!t) return "—";
  if (t.length > 16) return t.slice(0, 8) + "…" + t.slice(-4);
  return t;
};
const shortUri = (u) => {
  if (!u) return "—";
  if (u.length > 20) return u.slice(0, 10) + "…" + u.slice(-6);
  return u;
};

const OP_PILL_CLASS = {
  CredentialCreate: "pill pill-create",
  CredentialAccept: "pill pill-accept",
  CredentialDelete: "pill pill-delete",
  DIDSet: "pill pill-didset",
  DIDDelete: "pill pill-diddel",
};

let lastSuccessfulFetch = 0;
let lastErrorMsg = "";

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return await r.json();
}

function setConnState(ok, msg) {
  $("conn-dot").classList.toggle("good", ok);
  $("conn-dot").classList.toggle("bad", !ok);
  $("conn-text").textContent = msg;
}

async function refresh() {
  try {
    const [summary, creds, ids, watch, agents, health] = await Promise.all([
      getJSON("/api/summary"),
      getJSON("/api/credentials?limit=25"),
      getJSON("/api/identities?limit=25"),
      getJSON("/api/watchlist"),
      getJSON("/api/agent_state"),
      getJSON("/api/health?limit=15"),
    ]);

    // Summary
    $("stat-id").textContent = summary.identity_events ?? 0;
    $("stat-cred").textContent = summary.credential_events ?? 0;
    $("stat-watch").textContent = summary.watchlist_count ?? 0;
    $("stat-when").textContent = fmtTime(summary.now);

    // Agents
    const agentsEl = $("agents");
    if (!agents.agents || agents.agents.length === 0) {
      agentsEl.innerHTML =
        '<div class="empty">No agents watched yet. Insert into watchlist table to track.</div>';
    } else {
      agentsEl.innerHTML = "";
      for (const a of agents.agents) {
        const card = document.createElement("div");
        card.className = "agent-card";
        const didStatus = a.did_status
          ? `<span class="muted">DID: ${a.did_status.op_type} · ${fmtTime(a.did_status.received_at)}</span>`
          : '<span class="muted">No DID event yet</span>';
        const credsHtml = a.credentials.length === 0
          ? '<div class="muted mono" style="font-size:11px;margin-top:6px">No credential events</div>'
          : `<div class="cred-list">${a.credentials
              .map((c) => {
                const cls =
                  c.op_type === "CredentialDelete" ? "cred-item revoked"
                  : c.op_type === "CredentialAccept" ? "cred-item accepted"
                  : "cred-item";
                const op =
                  c.op_type === "CredentialCreate" ? "issued"
                  : c.op_type === "CredentialAccept" ? "accepted"
                  : c.op_type === "CredentialDelete" ? "deleted"
                  : c.op_type;
                return `<div class="${cls}">
                  <span class="pill ${c.op_type === 'CredentialCreate' ? 'pill-create' : c.op_type === 'CredentialAccept' ? 'pill-accept' : 'pill-delete'}">${op}</span>
                  <span>${shortType(c.credential_type || c.credential_type_hex || "—")}</span>
                  <span class="muted">${shortAddr(c.issuer)} → ${shortAddr(c.subject)}</span>
                  <span class="muted">${fmtTime(c.received_at)}</span>
                </div>`;
              })
              .join("")}</div>`;
        const roleClass = a.role === "issuer" ? "issuer" : a.role === "observer" ? "observer" : "";
        card.innerHTML = `
          <h3>
            <span class="role ${roleClass}">${a.role}</span>
            ${a.label ? `<span>${a.label}</span>` : ""}
            <span class="addr">${a.address}</span>
          </h3>
          <div>${didStatus}</div>
          ${credsHtml}
        `;
        agentsEl.appendChild(card);
      }
    }

    // Watchlist
    const wBody = document.querySelector("#watchlist tbody");
    wBody.innerHTML = (watch.watchlist || [])
      .map(
        (w) => `<tr>
          <td class="mono">${w.address}</td>
          <td>${w.role}</td>
          <td>${w.label || ""}</td>
          <td>${fmtTime(w.added_at)}</td>
        </tr>`
      )
      .join("") || '<tr><td colspan="4" class="empty">watchlist empty</td></tr>';

    // Credentials table
    const cBody = document.querySelector("#creds tbody");
    cBody.innerHTML = (creds.events || [])
      .map(
        (c) => `<tr>
          <td>${fmtTime(c.received_at)}</td>
          <td><span class="${OP_PILL_CLASS[c.op_type] || 'pill pill-other'}">${c.op_type.replace("Credential", "")}</span></td>
          <td class="mono">${shortAddr(c.issuer)}</td>
          <td class="mono">${shortAddr(c.subject)}</td>
          <td class="mono">${shortType(c.credential_type || c.credential_type_hex)}</td>
          <td class="mono">${shortUri(c.uri_hex)}</td>
          <td><a href="${BITHOMP_TEST(c.tx_hash)}" target="_blank">${shortHash(c.tx_hash)}</a></td>
        </tr>`
      )
      .join("") || '<tr><td colspan="7" class="empty">no credential events yet</td></tr>';

    // Identities table
    const iBody = document.querySelector("#ids tbody");
    iBody.innerHTML = (ids.events || [])
      .map(
        (i) => `<tr>
          <td>${fmtTime(i.received_at)}</td>
          <td><span class="${OP_PILL_CLASS[i.op_type] || 'pill pill-other'}">${i.op_type}</span></td>
          <td class="mono">${shortAddr(i.account)}</td>
          <td class="mono">${i.did || "—"}</td>
          <td><a href="${BITHOMP_TEST(i.tx_hash)}" target="_blank">${shortHash(i.tx_hash)}</a></td>
        </tr>`
      )
      .join("") || '<tr><td colspan="5" class="empty">no identity events yet</td></tr>';

    // Health
    const hBody = document.querySelector("#health tbody");
    hBody.innerHTML = (health.events || [])
      .map(
        (e) => `<tr>
          <td>${fmtTime(e.fired_at)}</td>
          <td>${e.event_type}</td>
        </tr>`
      )
      .join("") || '<tr><td colspan="2" class="empty">no monitor events yet</td></tr>';

    lastSuccessfulFetch = Math.floor(Date.now() / 1000);
    setConnState(true, `connected · ${summary.identity_events + summary.credential_events} events`);
  } catch (e) {
    lastErrorMsg = e.message || String(e);
    setConnState(false, `error: ${lastErrorMsg}`);
  }
}

// Render the "Project files" tree from /api/files.
// Files change rarely (not per-tx), so fetch once on load + every 60s.
async function refreshFileTree() {
  try {
    const data = await getJSON("/api/files");
    const rootEl = $("proj-root");
    rootEl.innerHTML = `<strong>v${data.version}</strong> · ${data.root}`;

    const tree = $("file-tree");
    if (!data.files || data.files.length === 0) {
      tree.innerHTML = '<div class="empty">No files found.</div>';
      return;
    }

    // Group by top-level dir
    const groups = new Map();
    for (const f of data.files) {
      const parts = f.rel_path.split("/");
      const top = parts.length > 1 ? parts[0] : "(root)";
      if (!groups.has(top)) groups.set(top, []);
      groups.get(top).push(f);
    }

    // Stable ordering: package/ code first, then tests/scripts/docs/etc.
    const ORDER = [
      "xrpl_agent_id", "tests", "scripts", "docs",
      "results", "build", "dist", ".gitignore", "LICENSE",
      "pyproject.toml", "README.md", "CHANGELOG.md",
    ];
    const sortedTops = [...groups.keys()].sort((a, b) => {
      const ai = ORDER.indexOf(a), bi = ORDER.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });

    let html = "";
    for (const top of sortedTops) {
      const files = groups.get(top);
      // Highlight common code/test/docs dirs
      const isImportant = ["xrpl_agent_id", "tests", "scripts", "docs"].includes(top);
      const openAttr = isImportant ? " open" : "";
      html += `<details${openAttr}><summary>${top}/ <span class="muted">(${files.length})</span></summary><ul>`;
      for (const f of files) {
        const leaf = f.rel_path.split("/").slice(1).join("/") || f.rel_path;
        const kb = (f.size_bytes / 1024).toFixed(1);
        const revealUrl = `file://${f.abs_path}`;
        html += `<li>
          <span class="name" title="${f.rel_path}">${leaf}</span>
          <span class="size">${kb} KB</span>
          <a class="reveal" href="${revealUrl}" target="_blank" title="Open in Finder">open</a>
        </li>`;
      }
      html += `</ul></details>`;
    }
    tree.innerHTML = html;
  } catch (e) {
    $("file-tree").innerHTML = `<div class="empty">Could not load file tree: ${e.message || e}</div>`;
  }
}

refreshFileTree();
setInterval(refreshFileTree, 60000);

// Stale-state warning: if no successful refresh in 30s, mark connection bad
setInterval(() => {
  if (lastSuccessfulFetch && Date.now() / 1000 - lastSuccessfulFetch > 30) {
    setConnState(false, "stale — refresh failed");
  }
}, 5000);

refresh();
setInterval(refresh, REFRESH_MS);
