/**
 * API-обёртка для Mini App.
 * Все запросы к /api/* с Telegram initData в заголовке.
 */
const API = (() => {
  const initData = window.Telegram?.WebApp?.initData || "";

  async function request(url, options = {}) {
    const headers = {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      ...(options.headers || {}),
    };
    const resp = await fetch(url, { ...options, headers });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || "Request failed");
    }
    return resp.json();
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
    deletePlaylist: (id) =>
      request(`/api/playlist/${id}`, { method: "DELETE" }),
  };
})();
