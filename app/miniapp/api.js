/**
 * API-обёртка для Mini App.
 * Все запросы к /api/* с Telegram initData в заголовке.
 */
const API = (() => {
  const initData = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || "";

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    const headers = {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      ...(options.headers || {}),
    };
    try {
      const resp = await fetch(url, { ...options, headers, signal: controller.signal });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || "Request failed");
      }
      return resp.json();
    } finally {
      clearTimeout(timeoutId);
    }
  }

  return {
    getMe: () => request("/api/me"),
    getHistory: () => request("/api/history"),
    getTop20: () => request("/api/top20"),
    getConnectUrl: () => request("/api/connect-url"),
    generate: (prompt) =>
      request("/api/generate", {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }),
    createPlaylist: ({ uris, name, prompt, source }) =>
      request("/api/create-playlist", {
        method: "POST",
        body: JSON.stringify({ uris, name, prompt, source }),
      }),
    search: (query) =>
      request("/api/search", {
        method: "POST",
        body: JSON.stringify({ query }),
      }),
    disconnect: () => request("/api/disconnect", { method: "POST" }),
    addToPlaylist: ({ spotifyPlaylistId, uris }) =>
      request("/api/add-to-playlist", {
        method: "POST",
        body: JSON.stringify({ spotify_playlist_id: spotifyPlaylistId, uris }),
      }),
    deletePlaylist: (id) =>
      request(`/api/playlist/${id}`, { method: "DELETE" }),
  };
})();
