/**
 * CatchTheWave — Telegram Mini App
 */
const App = (() => {
  const tg = window.Telegram?.WebApp;
  let currentTab = "home";
  let aiPreview = null;
  let top20Tracks = null;
  let selectedTrack = null;

  const PRESETS = {
    chill: "Calm evening music for relaxation, light beats, dreamy atmosphere.",
    workout: "Energetic workout music, heavy beats, drive, electronic and hip-hop.",
    focus: "Calm instrumental music for concentration: lo-fi, post-rock, ambient.",
    party: "Dance music for a party, popular hits, high tempo, disco and house.",
    ru_rap: "Modern Russian rap: notable artists, dark and lyrical vibe, diverse producers.",
    retro_80: "80s hits: synthwave, new wave, pop-rock, retro sound.",
  };

  function init() {
    if (tg) {
      tg.ready();
      tg.expand();
    }
    loadHome();
  }

  /* ── Navigation ────────────────────────────────────── */

  function switchTab(tab) {
    const tabMap = { top20: "top20", search: "search" };
    const pageId = tabMap[tab] || tab;

    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    const page = document.getElementById("page-" + pageId);
    if (page) page.classList.add("active");

    const mainTabs = ["home", "ai", "history", "profile"];
    document.querySelectorAll(".tab-bar button").forEach((btn, i) => {
      btn.classList.toggle("active", mainTabs[i] === tab);
    });

    currentTab = tab;
    if (tab === "history") loadHistory();
    if (tab === "profile") loadProfile();
    if (tab === "top20") loadTop20();
  }

  /* ── Toast ─────────────────────────────────────────── */

  function toast(msg, duration = 3000) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    setTimeout(() => el.classList.remove("show"), duration);
  }

  /* ── Home ──────────────────────────────────────────── */

  async function loadHome() {
    const el = document.getElementById("home-status");
    try {
      const data = await API.getMe();
      if (data.connected) {
        el.innerHTML =
          '<div class="status-badge status-connected">Connected to Spotify</div>' +
          '<div style="margin-top:6px;font-size:13px;color:var(--hint)">ID: ' +
          escapeHtml(data.spotify_user_id) + "</div>";
      } else {
        el.innerHTML =
          '<div class="status-badge status-disconnected">Not connected</div>' +
          '<div style="margin-top:10px"><button class="btn btn-primary btn-small" onclick="App.connectSpotify()">Connect Spotify</button></div>';
      }
    } catch (e) {
      el.innerHTML = '<div style="color:var(--danger)">Error loading status</div>';
    }
  }

  /* ── Profile ───────────────────────────────────────── */

  async function loadProfile() {
    const el = document.getElementById("profile-content");
    const connectBtn = document.getElementById("profile-connect-btn");
    try {
      const data = await API.getMe();
      if (data.connected) {
        el.innerHTML =
          '<div style="font-weight:700;font-size:16px">Spotify Account</div>' +
          '<div style="margin-top:8px"><span class="status-badge status-connected">Connected</span></div>' +
          '<div style="margin-top:8px;font-size:13px;color:var(--hint)">ID: ' +
          escapeHtml(data.spotify_user_id) + "</div>";
        connectBtn.style.display = "none";
      } else {
        el.innerHTML =
          '<div style="font-weight:700;font-size:16px">Spotify Account</div>' +
          '<div style="margin-top:8px"><span class="status-badge status-disconnected">Not connected</span></div>';
        connectBtn.style.display = "block";
      }
    } catch (e) {
      el.innerHTML = '<div style="color:var(--danger)">Error loading profile</div>';
    }
  }

  async function connectSpotify() {
    try {
      const data = await API.getConnectUrl();
      if (data.url) {
        if (tg) tg.openLink(data.url);
        else window.open(data.url, "_blank");
      }
    } catch (e) {
      toast("Error: " + e.message);
    }
  }

  /* ── AI Playlist ───────────────────────────────────── */

  function usePreset(key) {
    const prompt = PRESETS[key];
    if (prompt) {
      document.getElementById("ai-prompt").value = prompt;
      generatePlaylist();
    }
  }

  async function generatePlaylist() {
    const promptEl = document.getElementById("ai-prompt");
    const prompt = promptEl.value.trim();
    if (!prompt) { toast("Enter a description"); return; }

    showAiLoading(true);
    hideEl("ai-preview");

    try {
      const data = await API.generate(prompt);
      if (data.error) { toast(data.error); showAiLoading(false); return; }
      aiPreview = { tracks: data.tracks, total: data.total, prompt: data.prompt || prompt };
      renderAiPreview();
    } catch (e) {
      toast("Error: " + e.message);
    }
    showAiLoading(false);
  }

  function renderAiPreview() {
    if (!aiPreview) return;
    const list = document.getElementById("ai-track-list");
    const info = document.getElementById("ai-preview-info");
    info.textContent = `${aiPreview.tracks.length} of ${aiPreview.total} found`;
    list.innerHTML = aiPreview.tracks
      .map((t, i) =>
        `<li class="track-item" style="animation-delay:${i * 30}ms">
          <span class="num">${i + 1}</span>
          <div class="track-info">
            <div class="track-name">${escapeHtml(t.label || t.name || "")}</div>
          </div>
        </li>`)
      .join("");
    showEl("ai-preview");
    hideEl("ai-input-section");
  }

  async function createAiPlaylist() {
    if (!aiPreview) return;
    const uris = aiPreview.tracks.map((t) => t.uri);
    const prompt = aiPreview.prompt;
    const name = "AI: " + (prompt.length > 40 ? prompt.slice(0, 37) + "..." : prompt);

    try {
      const data = await API.createPlaylist({ uris, name, prompt, source: "ai" });
      toast("Playlist created!");
      if (data.url && tg) tg.openLink(data.url);
      cancelPreview();
    } catch (e) {
      toast("Error: " + e.message);
    }
  }

  function regeneratePlaylist() {
    if (!aiPreview) return;
    document.getElementById("ai-prompt").value = aiPreview.prompt;
    cancelPreview();
    generatePlaylist();
  }

  function cancelPreview() {
    aiPreview = null;
    hideEl("ai-preview");
    showEl("ai-input-section");
  }

  /* ── Top-20 ────────────────────────────────────────── */

  async function loadTop20() {
    showEl("top20-loading");
    hideEl("top20-preview");
    try {
      const data = await API.getTop20();
      top20Tracks = data.tracks;
      const list = document.getElementById("top20-track-list");
      list.innerHTML = top20Tracks
        .map((t, i) =>
          `<li class="track-item" style="animation-delay:${i * 30}ms">
            <span class="num">${i + 1}</span>
            <div class="track-info">
              <div class="track-name">${escapeHtml(t.label || "")}</div>
            </div>
          </li>`)
        .join("");
      hideEl("top20-loading");
      showEl("top20-preview");
    } catch (e) {
      hideEl("top20-loading");
      toast("Error: " + e.message);
    }
  }

  async function createTop20Playlist() {
    if (!top20Tracks) return;
    const uris = top20Tracks.map((t) => t.uri);
    try {
      const data = await API.createPlaylist({ uris, name: "CatchTheWave — Top 20", source: "top20" });
      toast("Playlist created!");
      if (data.url && tg) tg.openLink(data.url);
      switchTab("home");
    } catch (e) {
      toast("Error: " + e.message);
    }
  }

  /* ── Search / Add Song ─────────────────────────────── */

  async function searchTrack() {
    const input = document.getElementById("search-input");
    const query = input.value.trim();
    if (!query) return;

    showEl("search-loading");
    hideEl("search-results");
    hideEl("search-preview");

    try {
      const data = await API.search(query);
      hideEl("search-loading");
      const container = document.getElementById("search-results");
      if (!data.tracks || data.tracks.length === 0) {
        container.innerHTML = '<div class="empty-state" style="padding:24px 0"><div class="empty-title">No tracks found</div></div>';
        showEl("search-results");
        return;
      }
      container.innerHTML = data.tracks
        .map((t) =>
          `<div class="search-result" onclick='App.selectTrack(${JSON.stringify(t).replace(/'/g, "&#39;")})'>
            <div class="sr-info">
              <div class="sr-name">${escapeHtml(t.name)}</div>
              <div class="sr-artist">${escapeHtml(t.artists)}</div>
            </div>
          </div>`)
        .join("");
      showEl("search-results");
    } catch (e) {
      hideEl("search-loading");
      toast("Error: " + e.message);
    }
  }

  function selectTrack(track) {
    selectedTrack = track;
    hideEl("search-results");
    const info = document.getElementById("search-track-info");
    info.innerHTML =
      `<div style="font-weight:600">${escapeHtml(track.name)}</div>` +
      `<div style="font-size:13px;color:var(--hint);margin-top:4px">${escapeHtml(track.artists)}</div>`;
    showEl("search-preview");
  }

  async function createTrackPlaylist() {
    if (!selectedTrack) return;
    try {
      const name = `${selectedTrack.artists} — ${selectedTrack.name}`;
      const data = await API.createPlaylist({
        uris: [selectedTrack.uri],
        name: name.length > 60 ? name.slice(0, 57) + "..." : name,
        source: "track",
      });
      toast("Playlist created!");
      if (data.url && tg) tg.openLink(data.url);
      cancelSearchPreview();
    } catch (e) {
      toast("Error: " + e.message);
    }
  }

  function cancelSearchPreview() {
    selectedTrack = null;
    hideEl("search-preview");
    hideEl("search-playlist-picker");
    document.getElementById("search-results").innerHTML = "";
  }

  async function showPlaylistPicker() {
    if (!selectedTrack) return;
    hideEl("search-preview");

    const container = document.getElementById("picker-list");
    container.innerHTML = '<div class="loader" style="padding:16px 0"><div class="spinner"></div></div>';
    showEl("search-playlist-picker");

    try {
      const data = await API.getHistory();
      const playlists = (data.playlists || []).filter((p) => p.spotify_playlist_id);

      if (playlists.length === 0) {
        container.innerHTML = '<div style="text-align:center;color:var(--hint);padding:16px 0;font-size:13px">No playlists yet. Create one first.</div>';
        return;
      }

      container.innerHTML = playlists
        .map((p) => {
          const icon = p.source === "ai" ? "&#129302;" : p.source === "top20" ? "&#128293;" : "&#127925;";
          const count = p.tracks_count ? p.tracks_count + " tracks" : "";
          return `<button class="picker-item" onclick="App.addToExistingPlaylist('${escapeHtml(p.spotify_playlist_id)}')">
            <div class="pi-icon">${icon}</div>
            <div class="pi-info">
              <div class="pi-name">${escapeHtml(p.name || "Untitled")}</div>
              <div class="pi-meta">${count}</div>
            </div>
          </button>`;
        })
        .join("");
    } catch (e) {
      container.innerHTML = '<div style="color:var(--danger);text-align:center;padding:16px 0">Error loading playlists</div>';
    }
  }

  function hidePlaylistPicker() {
    hideEl("search-playlist-picker");
    showEl("search-preview");
  }

  async function addToExistingPlaylist(spotifyPlaylistId) {
    if (!selectedTrack) return;
    try {
      await API.addToPlaylist({ spotifyPlaylistId, uris: [selectedTrack.uri] });
      toast("Track added!");
      cancelSearchPreview();
    } catch (e) {
      toast("Error: " + e.message);
    }
  }

  /* ── History ───────────────────────────────────────── */

  async function loadHistory() {
    showEl("history-loading");
    hideEl("history-empty");
    document.getElementById("history-list").innerHTML = "";

    try {
      const data = await API.getHistory();
      hideEl("history-loading");

      if (!data.playlists || data.playlists.length === 0) {
        showEl("history-empty");
        return;
      }

      const container = document.getElementById("history-list");
      container.innerHTML = data.playlists
        .map((p) => {
          const date = (p.created_at || "").slice(0, 19).replace("T", " ");
          const sourceIcon =
            p.source === "ai" ? "&#129302;"
            : p.source === "top20" ? "&#128293;"
            : "&#127925;";
          const promptHtml = p.prompt
            ? `<div class="prompt-text">${escapeHtml(p.prompt.slice(0, 80))}${p.prompt.length > 80 ? "..." : ""}</div>`
            : "";
          const openBtn = p.url
            ? `<a href="${escapeHtml(p.url)}" class="btn-open" target="_blank" ${tg ? `onclick="event.preventDefault();Telegram.WebApp.openLink('${escapeHtml(p.url)}')"` : ""}>Open</a>`
            : "";
          const repeatBtn = p.source === "ai" && p.prompt
            ? `<button class="btn-repeat" onclick="App.repeatFromHistory('${escapeHtml(p.prompt.replace(/'/g, "\\'"))}')">Repeat</button>`
            : "";
          const deleteBtn = `<button class="btn-delete" onclick="App.deletePlaylist(${p.id}, this)">Delete</button>`;

          return `<div class="card playlist-card" data-id="${p.id}">
            <div class="cover">${sourceIcon}</div>
            <div class="info">
              <div class="name">${escapeHtml(p.name || "Untitled")}</div>
              <div class="meta">${date}${p.tracks_count ? " &bull; " + p.tracks_count + " tracks" : ""}</div>
              ${promptHtml}
              <div class="actions">${openBtn}${repeatBtn}${deleteBtn}</div>
            </div>
          </div>`;
        })
        .join("");
    } catch (e) {
      hideEl("history-loading");
      toast("Error: " + e.message);
    }
  }

  function repeatFromHistory(prompt) {
    switchTab("ai");
    document.getElementById("ai-prompt").value = prompt;
    generatePlaylist();
  }

  async function deletePlaylist(id, btnEl) {
    const doDelete = () => {
      return API.deletePlaylist(id).then(() => {
        toast("Playlist removed");
        const card = btnEl.closest(".playlist-card");
        if (card) {
          card.classList.add("removing");
          setTimeout(() => card.remove(), 300);
        }
      }).catch((e) => {
        toast("Error: " + e.message);
      });
    };

    if (tg && tg.showConfirm) {
      tg.showConfirm("Remove this playlist from Spotify?", (ok) => {
        if (ok) doDelete();
      });
    } else {
      if (confirm("Remove this playlist from Spotify?")) doDelete();
    }
  }

  /* ── Helpers ───────────────────────────────────────── */

  function showEl(id) {
    const el = document.getElementById(id);
    if (el) { el.style.display = ""; el.classList.add("visible"); }
  }

  function hideEl(id) {
    const el = document.getElementById(id);
    if (el) { el.style.display = "none"; el.classList.remove("visible"); }
  }

  function showAiLoading(show) {
    if (show) { showEl("ai-loading"); hideEl("ai-input-section"); }
    else { hideEl("ai-loading"); }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  init();

  return {
    switchTab, usePreset, generatePlaylist, createAiPlaylist,
    regeneratePlaylist, cancelPreview, createTop20Playlist,
    searchTrack, selectTrack, createTrackPlaylist, cancelSearchPreview,
    showPlaylistPicker, hidePlaylistPicker, addToExistingPlaylist,
    connectSpotify, repeatFromHistory, deletePlaylist,
  };
})();
