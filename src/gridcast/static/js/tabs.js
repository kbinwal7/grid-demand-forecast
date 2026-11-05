export const TAB_IDS = Object.freeze([
  "forecast",
  "accuracy",
  "failures",
  "models",
  "data-health",
]);

export function tabFromHash(hash) {
  const requested = String(hash || "").replace(/^#/, "");
  return TAB_IDS.includes(requested) ? requested : "forecast";
}

export function createTabLoader(loaders) {
  const loaded = new Set();
  const pending = new Map();

  async function load(tab, { refresh = false } = {}) {
    if (!TAB_IDS.includes(tab) || typeof loaders[tab] !== "function") {
      throw new Error(`Unknown dashboard tab: ${tab}`);
    }
    if (!refresh && loaded.has(tab)) {
      return;
    }
    if (pending.has(tab)) {
      return pending.get(tab);
    }

    const request = Promise.resolve().then(() => loaders[tab]({ refresh }));
    pending.set(tab, request);
    try {
      await request;
      loaded.add(tab);
    } catch (error) {
      loaded.delete(tab);
      throw error;
    } finally {
      pending.delete(tab);
    }
  }

  load.invalidate = (tab) => {
    if (tab) {
      loaded.delete(tab);
      return;
    }
    loaded.clear();
  };
  return load;
}
