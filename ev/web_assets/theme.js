/* Apply the saved theme before the stylesheet paints; no network dependency. */
(() => {
  const key = "expvault.web.theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const names = {
    light: "浅色",
    dark: "深色",
    jade: "青竹",
    sand: "暖砂",
    lavender: "雾紫",
    ocean: "深海",
    system: "跟随系统",
  };
  const valid = (value) => Object.prototype.hasOwnProperty.call(names, value);
  let preference = "light";
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) preference = saved;
  } catch {
    /* The switch still works when browser storage is unavailable. */
  }
  const incomingURL = new URL(location.href);
  const incoming = incomingURL.searchParams.get("theme");
  if (valid(incoming)) {
    preference = incoming;
    try {
      localStorage.setItem(key, preference);
    } catch {}
    incomingURL.searchParams.delete("theme");
    history.replaceState(null, "", incomingURL.href);
  }
  function apply() {
    document.documentElement.dataset.theme =
      preference === "system" ? (media.matches ? "dark" : "light") : preference;
    document
      .querySelectorAll("[data-theme-select]")
      .forEach((select) => (select.value = preference));
    const status = document.getElementById("theme-status");
    if (status)
      status.textContent =
        preference === "system"
          ? "主题跟随系统设置"
          : `当前为${names[preference]}主题`;
  }
  apply();
  document.addEventListener("reset", () => queueMicrotask(apply));
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.querySelectorAll("[data-theme-select]").forEach((select) =>
      select.addEventListener("change", (event) => {
        if (!valid(event.target.value)) return;
        preference = event.target.value;
        try {
          localStorage.setItem(key, preference);
        } catch {}
        apply();
      }),
    );
  });
  const changed = () => {
    if (preference === "system") apply();
  };
  if (media.addEventListener) media.addEventListener("change", changed);
  else media.addListener(changed);
  window.addEventListener("storage", (event) => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : "light";
    apply();
  });
})();
