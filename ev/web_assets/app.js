"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
const e = escapeHTML;
function scientificHTML(value, depth = 0) {
  const text = String(value ?? "");
  if (depth >= 6) return e(text);
  const pairs = new Map(),
    stack = [];
  for (let position = 0; position < text.length; position++) {
    if (text[position] === "{") stack.push(position);
    else if (text[position] === "}" && stack.length)
      pairs.set(stack.pop(), position);
  }
  let result = "",
    start = 0,
    index = 0;
  while (index < text.length - 1) {
    if (
      (text[index] === "_" || text[index] === "^") &&
      text[index + 1] === "{"
    ) {
      const closing = pairs.get(index + 1);
      if (closing !== undefined) {
        const end = closing + 1;
        const tag = text[index] === "_" ? "sub" : "sup";
        result +=
          e(text.slice(start, index)) +
          `<${tag}>` +
          scientificHTML(text.slice(index + 2, end - 1), depth + 1) +
          `</${tag}>`;
        start = index = end;
        continue;
      }
    }
    index++;
  }
  return result + e(text.slice(start));
}
function notesHTML(value) {
  const parts = String(value ?? "").split("**");
  let result = scientificHTML(parts[0]);
  for (let index = 1; index < parts.length; index += 2) {
    result +=
      index + 1 < parts.length && parts[index]
        ? `<strong>${scientificHTML(parts[index])}</strong>${scientificHTML(parts[index + 1])}`
        : scientificHTML("**" + parts.slice(index, index + 2).join("**"));
  }
  return result;
}
function setupScientificInputs() {
  document
    .querySelectorAll("[data-scientific]:not([data-scientific-ready])")
    .forEach((input) => {
      input.dataset.scientificReady = "1";
      const wrapper = document.createElement("div");
      wrapper.className = "scientific-editor";
      input.before(wrapper);
      wrapper.append(input);
      const label =
        input.getAttribute("aria-label") ||
        document.querySelector(`label[for="${input.id}"]`)?.textContent ||
        "文字";
      const toolbar = document.createElement("div");
      toolbar.className = "scientific-toolbar";
      toolbar.innerHTML = `<button type="button" data-script="sub" aria-label="${e(label)}：下标">x<sub>2</sub> 下标</button><button type="button" data-script="sup" aria-label="${e(label)}：上标">x<sup>2</sup> 上标</button>`;
      if (input.hasAttribute("data-rich-notes"))
        toolbar.insertAdjacentHTML(
          "beforeend",
          `<button type="button" data-script="bold" aria-label="${e(label)}：加粗"><strong>B</strong> 加粗</button>`,
        );
      const preview = document.createElement("div");
      preview.className = "scientific-preview";
      preview.setAttribute("aria-label", label + "：预览");
      const update = () => {
        preview.innerHTML = input.hasAttribute("data-rich-notes")
          ? notesHTML(input.value)
          : scientificHTML(input.value);
        preview.hidden = !input.value;
      };
      toolbar.addEventListener("click", (event) => {
        const button = event.target.closest("[data-script]");
        if (!button) return;
        const start = input.selectionStart ?? input.value.length,
          end = input.selectionEnd ?? start;
        const selected = input.value.slice(start, end),
          marker = button.dataset.script === "sub" ? "_" : "^";
        input.setRangeText(
          button.dataset.script === "bold"
            ? "**" + selected + "**"
            : marker + "{" + selected + "}",
          start,
          end,
          "end",
        );
        input.focus();
        input.setSelectionRange(start + 2, start + 2 + selected.length);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      input.addEventListener("input", update);
      wrapper.append(toolbar, preview);
      update();
    });
}
const iconPaths = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  flask:
    '<path d="M9 3h6m-5 0v7l-6 9a1.3 1.3 0 0 0 1 2h14a1.3 1.3 0 0 0 1-2l-6-9V3M7 15h10"/>',
  folder:
    '<path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z"/>',
  shield:
    '<path d="m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  link: '<path d="m9 15 6-6m-8 4-2 2a4 4 0 0 0 6 6l3-3m-1-11 2-2a4 4 0 0 1 6 6l-3 3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  back: '<path d="M19 12H5m5-5-5 5 5 5"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  file: '<path d="M14 3H5v18h14V8Zm0 0v6h5M8 13h8M8 17h6"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 5 5 3-3 4 4"/>',
  video:
    '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3Z"/>',
  edit: '<path d="m15 4 5 5M4 20l4-1L20 7a2 2 0 0 0-4-4L4 15ZM4 20h16"/>',
  tag: '<path d="m3 3 9 1 9 9-8 8-9-9Z"/><circle cx="8" cy="8" r="1"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  note: '<path d="M4 3h16v18H4Zm4 5h8m-8 4h8m-8 4h5"/>',
  target:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  archive: '<path d="M3 3h18v5H3Zm2 5v13h14V8m-10 4h6"/>',
  refresh: '<path d="M20 8a8 8 0 1 0 0 9M20 3v6h-6"/>',
  export: '<path d="M14 3h7v7m0-7L10 14M9 4H4v17h17v-6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  settings:
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
};
iconPaths.theme =
  '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16Z"/>';
iconPaths.pin = '<path d="m16 3 5 5-4 1-3 5-4-4 5-3Z M12 12l-7 7"/>';
iconPaths.copy =
  '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>';
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${iconPaths[name] || iconPaths.file}</svg>`;
document.querySelectorAll("[data-icon]").forEach((el) => {
  el.innerHTML = icon(el.dataset.icon);
});
const statuses = {
  running: "进行中",
  done: "已完成",
  planned: "计划中",
  aborted: "已中止",
  archived: "已归档",
};
const operationNames = {
  "experiments.create": "新建实验",
  "experiments.update": "保存实验",
  "experiments.archive": "归档 / 恢复",
  "experiments.duplicate": "复制实验",
  "experiments.batch": "按模板批量生成",
  "experiments.groups": "批量编组",
  "groups.rename": "重命名实验分组",
  "groups.dissolve": "解散实验分组",
  "notes.add": "追加日志",
  "notes.references": "修改日志引用",
  "steps.add": "记录步骤",
  "steps.update": "修改步骤",
  "files.import": "导入文件",
  "files.tags": "更新文件标签",
  "files.remove": "删除文件",
  "experiments.scan": "扫描文件",
  "experiments.verify": "校验实验",
  "backup.create": "创建备份",
  "reports.export": "导出台账",
  "uploads.discard": "清理暂存",
};
const eventNames = {
  from_template: "由模板创建",
  create: "新建实验",
  edit: "更新实验",
  gui_import: "导入资料",
  add: "添加文件",
  step_add: "记录步骤",
  step_edit: "修改步骤",
  note: "追加日志",
  note_references: "修改日志引用",
  backup: "创建备份",
  archive: "归档实验",
  unarchive: "恢复实验",
  rm: "删除文件",
  scan: "扫描文件",
};
const pendingStatuses = new Set(["queued", "running"]);
const state = {
  session: null,
  view: "overview",
  expId: "",
  tab: "summary",
  meta: null,
  files: [],
  listQuery: "",
  status: "",
  archived: false,
  page: 0,
  activePage: 0,
  fileQuery: "",
  fileRole: "",
  filePage: 0,
  selected: new Set(),
  experimentSelection: new Set(),
  experimentLabels: new Map(),
  groupFilter: "",
  jobs: new Map(),
  watchers: new Map(),
  generation: 0,
  dialog: null,
  queue: [],
  uploadXHR: null,
  parametersExpanded: false,
  parameterExpId: "",
};
let token =
  location.hash.slice(1) || sessionStorage.getItem("expvault.web.token") || "";
if (location.hash)
  history.replaceState(null, "", location.pathname + location.search);
if (token) sessionStorage.setItem("expvault.web.token", token);

function size(value) {
  let n = Number(value) || 0;
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (n < 1024 || unit === "TB")
      return `${n < 10 && unit !== "B" ? n.toFixed(1) : Math.round(n)} ${unit}`;
    n /= 1024;
  }
}
function date(value, time = false) {
  if (!value) return "—";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return String(value);
  return time
    ? d.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : String(value).slice(0, 10);
}
function tags(value) {
  return [
    ...new Set(
      String(value || "")
        .split(/[,，;；\n]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
}
const tagHTML = (values) =>
  (values || [])
    .map((t) => `<span class="tag" title="${e(t)}">${e(t)}</span>`)
    .join("");
const badge = (status) =>
  `<span class="badge ${Object.hasOwn(statuses, status) ? status : "planned"}">${e(statuses[status] || status)}</span>`;
function kind(path) {
  if (
    /\.(jpe?g|png|heic|heif|gif|webp|bmp|tiff?|dng|raw|cr2|nef|arw)$/i.test(
      path,
    )
  )
    return "图片";
  if (/\.(mov|mp4|m4v|avi|mkv|mts|m2ts|3gp|webm)$/i.test(path)) return "视频";
  return "数据";
}
const kindIcon = (path) =>
  icon(
    kind(path) === "图片" ? "image" : kind(path) === "视频" ? "video" : "file",
  );
function uuid() {
  const data = new Uint8Array(16);
  crypto.getRandomValues(data);
  return Array.from(data, (v) => v.toString(16).padStart(2, "0")).join("");
}
function query(values) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(values))
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  return params.toString();
}
const fileURL = (path, download = false, expId = state.expId) =>
  "/api/file?" +
  query({ exp_id: expId, path, download: download ? "1" : undefined });
const toast = (message, error = false) => {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.textContent = message;
  $("toasts").append(el);
  setTimeout(() => el.remove(), 6500);
};
function errorText(error) {
  return error?.message || "请求未完成，请检查连接后重试";
}
async function api(path, options = {}) {
  const headers = {
    "X-Expvault-Request": "1",
    ...(token ? { Authorization: "Bearer " + token } : {}),
    ...(options.headers || {}),
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch("/api/" + path, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || "操作未完成");
    error.code = result.code;
    error.data = result.data;
    error.status = response.status;
    throw error;
  }
  return result;
}

function routeFromURL() {
  const p = new URLSearchParams(location.search);
  state.view = [
    "overview",
    "experiments",
    "experiment",
    "files",
    "backups",
    "sync",
  ].includes(p.get("view"))
    ? p.get("view")
    : "overview";
  state.expId = p.get("exp_id") || "";
  state.tab = ["summary", "steps", "files", "history"].includes(p.get("tab"))
    ? p.get("tab")
    : "summary";
}
function navigate(view, expId = "", tab = "summary", replace = false) {
  if (view !== "experiments") clearExperimentSelection();
  state.view = view;
  state.expId = expId;
  state.tab = tab;
  state.page = 0;
  state.filePage = 0;
  state.fileQuery = "";
  state.fileRole = "";
  state.meta = null;
  const url =
    "/?" +
    query({ view, exp_id: expId, tab: tab === "summary" ? undefined : tab });
  history[replace ? "replaceState" : "pushState"](null, "", url);
  closeMenu();
  renderPage({ focusHeading: true });
  window.scrollTo({ top: 0, behavior: "instant" });
}
window.addEventListener("popstate", () => {
  routeFromURL();
  closeMenu();
  renderPage({ focusHeading: true });
});
// The dialog is mobile-only; the same sidebar returns to its desktop landmark.
const sidebarHome = document.createComment("sidebar home");
$("sidebar").before(sidebarHome);
const mobileNavigation = window.matchMedia("(max-width: 700px)");
function restoreSidebar() {
  if (sidebarHome.nextSibling !== $("sidebar")) sidebarHome.after($("sidebar"));
  $("menu-button").setAttribute("aria-expanded", "false");
}
function closeMenu() {
  if ($("navigation-drawer").open) $("navigation-drawer").close();
  restoreSidebar();
}
$("menu-button").addEventListener("click", () => {
  if (!mobileNavigation.matches || $("navigation-drawer").open) return;
  $("navigation-content").append($("sidebar"));
  $("navigation-drawer").showModal();
  $("menu-button").setAttribute("aria-expanded", "true");
});
$("menu-close").addEventListener("click", closeMenu);
$("navigation-drawer").addEventListener("close", () => {
  if (!$("navigation-drawer").open) restoreSidebar();
});
$("navigation-drawer").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeMenu();
});
$("navigation-drawer").addEventListener("click", (event) => {
  if (event.target === $("navigation-drawer")) closeMenu();
});
mobileNavigation.addEventListener("change", () => {
  const wasOpen = $("navigation-drawer").open;
  closeMenu();
  if (wasOpen && !mobileNavigation.matches)
    document.querySelector('.nav-item[aria-current="page"]')?.focus();
});
document.querySelector(".skip-link").addEventListener("click", (event) => {
  // Pairing uses the URL fragment, so skip navigation must not change it.
  event.preventDefault();
  ($("page-title") || $("content")).focus();
});
$("global-search").addEventListener("submit", (event) => {
  event.preventDefault();
  state.listQuery = $("global-query").value.trim();
  navigate("experiments");
});
$("tasks-toggle").addEventListener("click", () => {
  $("task-panel").hidden = !$("task-panel").hidden;
  renderTasks();
});
$("tasks-close").addEventListener("click", () => {
  $("task-panel").hidden = true;
});

async function boot() {
  try {
    state.session = await api("session", { method: "POST", body: "{}" });
    $("workspace-name").innerHTML = scientificHTML(
      state.session.workspace?.name || state.session.project,
    );
    $("workspace-name").title =
      state.session.workspace?.name || state.session.project;
    $("root-label").textContent = state.session.root;
    $("root-label").title = state.session.root;
    $("operator-avatar").textContent = (state.session.operator || "我").slice(
      0,
      1,
    );
    $("operator-avatar").title = state.session.operator || "本机使用者";
    if (state.session.storage) {
      $("sidebar").querySelector(".sidebar-tip strong").textContent =
        "资料保存在 NAS";
      $("sidebar").querySelector(".sidebar-tip p").textContent = state.session
        .storage.read_only
        ? "当前为只读查看。编辑请使用主机连接；共享断开后请先在系统中重新挂载。"
        : "当前为编辑主机。其他电脑可挂载同一共享，以只读方式查看。";
    }
    $("connection-label").textContent =
      location.hostname === "localhost" || location.hostname === "127.0.0.1"
        ? "本机工作台已连接"
        : "局域网工作台已连接";
    $("login").hidden = true;
    $("app").hidden = false;
    routeFromURL();
    await renderPage();
    restoreJobs();
  } catch (error) {
    $("login-message").textContent =
      error.status === 401
        ? errorText(error)
        : "暂时无法连接实验库，请确认电脑端的网页服务仍在运行。";
    $("login-form").hidden = false;
  }
}
$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("access-link").value.trim();
  token = value.includes("#") ? value.slice(value.indexOf("#") + 1) : value;
  sessionStorage.setItem("expvault.web.token", token);
  boot();
});

async function openWorkspaces() {
  const data = await api("workspaces");
  openDialog(
    "项目工作空间",
    `<div class="workspace-intro"><p>每个工作空间是一份独立的实验库，实验、文件、步骤、标签和备份分别保存。</p>${button("new-workspace", "新建工作空间", "plus", "", true)}</div><div class="workspace-list" role="list">${data.items.map((item) => `<article class="workspace-item ${item.id === data.current ? "current" : ""}" role="listitem"><div class="workspace-item-title"><span class="type-icon">${icon("folder")}</span><div><h3>${scientificHTML(item.name)}</h3><small>${e(item.project || "未设置默认课题")}${item.operator ? " · " + e(item.operator) : ""}</small></div>${item.id === data.current ? '<span class="badge running">当前空间</span>' : ""}</div><p class="workspace-path">${e(item.root)}</p>${!item.available ? `<p class="reference-warning">${e(item.error)}</p>` : ""}<footer><span>${item.id === "default" ? "原有实验库" : "独立实验库"}${item.created_at ? " · " + e(date(item.created_at)) : ""}</span><button type="button" class="button small ${item.id === data.current ? "" : "primary"}" data-action="select-workspace" data-id="${e(item.id)}" ${item.available ? "" : "disabled"}>${item.id === data.current ? "继续使用" : "进入工作空间"}</button></footer></article>`).join("")}</div><p class="field-hint">切换后，已开始的后台任务和手机接收会话继续在原空间运行。切回原空间可查看结果。</p>`,
    null,
    { wide: true, kicker: "独立项目与实验资料" },
  );
}
async function enterWorkspace(id) {
  if (state.uploadXHR)
    throw new Error("请先等待当前文件发送完成或停止发送，再切换工作空间");
  if (state.workspaceSwitching) return;
  const dialog = state.dialog;
  state.workspaceSwitching = true;
  try {
    const result = await api("workspaces/select", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    if (state.dialog !== dialog) {
      toast("工作空间已准备，可从列表重新进入");
      return;
    }
    if (!closeWorkspaceDialog()) return;
    if (id === state.session.workspace?.id) {
      toast("正在使用此工作空间");
      return;
    }
    const target = new URL(result.url);
    target.searchParams.set("theme", $("theme-select").value);
    location.assign(target.href);
  } finally {
    state.workspaceSwitching = false;
  }
}
function closeWorkspaceDialog() {
  closeDialog(true);
  return !$("editor").open;
}
function openWorkspaceCreator() {
  openDialog(
    "新建工作空间",
    `<p class="prose">为新项目建立空的实验库，实验与资料从这里分别管理。</p><div class="form-grid workspace-create-form">${field("workspace_name", "工作空间名称", "", { required: true, wide: true, hint: "例如：二维材料器件 / 电化学测试；名称最多 80 字。" })}${field("workspace_project", "默认课题", "", { wide: true, hint: "留空时使用工作空间名称。" })}${field("workspace_operator", "默认操作人", state.session.operator, { wide: true })}</div>`,
    async (form) => {
      const dialog = state.dialog;
      const item = await api("workspaces", {
        method: "POST",
        body: JSON.stringify({
          name: String(form.get("workspace_name")).trim(),
          project: String(form.get("workspace_project") || "").trim(),
          operator: String(form.get("workspace_operator") || "").trim(),
        }),
      });
      if (state.dialog !== dialog) {
        toast("工作空间已创建，可在列表中进入");
        return;
      }
      await enterWorkspace(item.id);
    },
    { wide: false, label: "创建并进入", kicker: "新的项目，独立的实验库" },
  );
  $("field-workspace_name").maxLength = 80;
  $("field-workspace_project").maxLength = 256;
  $("field-workspace_operator").maxLength = 256;
}

function pageHeading(kicker, title, description, actions = "") {
  return `<div class="page-heading"><div><div class="eyebrow">${e(kicker)}</div><h1>${e(title)}</h1><p>${e(description)}</p></div><div class="heading-actions">${actions}</div></div>`;
}
function empty(title, description, action = "") {
  return `<div class="empty-state">${icon("flask")}<h3>${e(title)}</h3><p>${e(description)}</p>${action}</div>`;
}
const nasWriteActions = new Set([
  "new-experiment",
  "edit-experiment",
  "edit-parameters",
  "edit-status",
  "add-instrument",
  "edit-instrument",
  "add-measurement",
  "edit-measurement",
  "copy-measurement",
  "duplicate-experiment",
  "batch-experiments",
  "import",
  "phone-import",
  "add-step",
  "edit-step",
  "add-note",
  "note-references",
  "edit-file-tags",
  "delete-files",
  "archive",
  "scan",
  "backup",
  "new-workspace",
  "bulk-add-groups",
  "bulk-remove-groups",
  "edit-experiment-groups",
  "rename-experiment-group",
  "dissolve-experiment-group",
]);
function nasReadOnly() {
  return !!state.session?.storage?.read_only;
}
function applyNASReadOnly() {
  if (!nasReadOnly()) return;
  document.querySelectorAll("[data-action]").forEach((control) => {
    if (nasWriteActions.has(control.dataset.action)) {
      control.disabled = true;
      control.title = "NAS 只读查看，请在编辑主机修改";
    }
  });
}
function nasBanner() {
  const storage = state.session?.storage;
  if (!storage) return "";
  return `<section class="nas-banner" aria-label="NAS 连接状态"><div><strong>${storage.read_only ? "NAS · 只读查看" : "NAS · 当前为编辑主机"}</strong><span class="nas-location">${e(storage.root)}</span><small>${storage.owner?.host ? "编辑主机：" + e(storage.owner.host) : "当前没有编辑主机"} · 资料直接从 NAS 读取</small></div>${button("nas-refresh", "刷新 NAS 资料", "refresh")}</section>`;
}
async function openNASLibraries() {
  const data = await api("nas/libraries");
  openDialog(
    "NAS 实验库",
    `<p class="prose">先在系统中挂载 NAS 共享文件夹，再选择连接方式。一台电脑负责编辑，其他电脑可只读查看同一份资料。</p><div class="nas-library-actions">${button("nas-connect", "连接 NAS 文件夹", "plus", "", true)}${data.home_url && new URL(data.home_url).origin !== location.origin ? `<a class="button" href="${e(data.home_url)}">返回起始实验库</a>` : ""}</div><div class="nas-connections">${data.items.length ? data.items.map((item) => `<article class="nas-connection"><h3>${scientificHTML(item.name)}</h3><p>${e(item.root)}</p><small>${item.available ? "共享目录可访问" : "共享暂不可访问，请先重新挂载"}</small><div class="toolbar-group">${button("nas-open", "只读打开", "folder", `data-id="${e(item.id)}" data-mode="viewer" ${item.available ? "" : "disabled"}`)}${button("nas-open", "作为编辑主机打开", "edit", `data-id="${e(item.id)}" data-mode="writer" ${item.available ? "" : "disabled"}`)}</div></article>`).join("") : '<p class="field-hint">当前电脑还没有保存 NAS 连接。连接记录仅保存在这台电脑。</p>'}</div>`,
    null,
    { wide: true, kicker: "系统已挂载的共享目录" },
  );
}
function openNASConnector() {
  openDialog(
    "连接 NAS 实验库",
    `<p class="field-hint">程序运行在本机，实验文件保存在 NAS。这里不保存 NAS 账号或密码；连接和权限由系统管理。</p><div class="form-grid">${field("nas_root", "NAS 挂载目录", "", { required: true, wide: true, hint: "Mac 例如 /Volumes/课题共享/实验库；Windows 粘贴网络驱动器或共享文件夹的完整路径。" })}${field("nas_name", "本机连接名称", "", { wide: true, hint: "例如 材料实验 NAS，留空沿用实验库名称。" })}<div class="form-field wide"><label for="nas-mode">连接方式</label><select id="nas-mode" name="nas_mode"><option value="viewer">只读查看（其他电脑）</option><option value="writer">编辑主机（同一时间一台）</option></select></div><label class="checkbox-label wide"><input type="checkbox" name="nas_initialize">首次建立或将已关闭的原库设为 NAS 实验库</label><p class="field-hint wide">首次建立请选择编辑主机，并确认原程序已关闭；现有实验文件会保留。普通只读连接不会初始化或修改共享目录。</p></div>`,
    async (form) => {
      await enterNAS({
        root: String(form.get("nas_root")).trim(),
        name: String(form.get("nas_name")).trim(),
        mode: form.get("nas_mode"),
        initialize: form.has("nas_initialize"),
      });
    },
    { wide: true, label: "连接并打开", kicker: "NAS" },
  );
  state.dialog.freezeOnSubmit = true;
}
async function enterNAS(values) {
  if (state.nasConnecting) throw new Error("正在连接 NAS，请稍候");
  if (state.uploadXHR)
    throw new Error("请先完成或停止当前文件发送，再切换实验库");
  const dialog = state.dialog;
  state.nasConnecting = true;
  try {
    const result = await api("nas/open", {
      method: "POST",
      body: JSON.stringify(values),
    });
    if (state.dialog !== dialog) {
      toast("NAS 已连接，可从 NAS 实验库重新打开");
      return;
    }
    closeDialog(true);
    const target = new URL(result.url);
    target.searchParams.set("theme", $("theme-select").value);
    location.assign(target.href);
  } finally {
    state.nasConnecting = false;
  }
}
function button(action, label, name = "plus", attrs = "", primary = false) {
  return `<button type="button" class="button${primary ? " primary" : ""}" data-action="${action}" ${nasReadOnly() && nasWriteActions.has(action) ? 'disabled title="NAS 只读查看，请在编辑主机修改"' : ""} ${attrs}>${icon(name)}${e(label)}</button>`;
}
const parameterPins = new Map();
function parameterPreferenceKey(expId) {
  return (
    "expvault.web.parameters:" +
    JSON.stringify([state.session?.root || "", expId])
  );
}
function pinnedParameters(expId) {
  const key = parameterPreferenceKey(expId);
  if (parameterPins.has(key)) return parameterPins.get(key);
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value)
      ? value.filter((key) => typeof key === "string")
      : [];
  } catch {
    return [];
  }
}
function orderedParameters(record) {
  const pins = pinnedParameters(record.id);
  return Object.entries(record.params || {}).sort(
    ([a], [b]) => Number(pins.includes(b)) - Number(pins.includes(a)),
  );
}
function parameterValue(value) {
  return value === "" || value == null
    ? "未填写"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
function compactParameters(record, limit = 2) {
  const values = orderedParameters(record);
  return values.length
    ? `<dl class="compact-parameters">${values
        .slice(0, limit)
        .map(
          ([key, value]) =>
            `<div><dt>${scientificHTML(key)}</dt><dd>${scientificHTML(parameterValue(value))}</dd></div>`,
        )
        .join(
          "",
        )}</dl>${values.length > limit ? `<span class="parameter-more">另 ${values.length - limit} 项参数</span>` : ""}`
    : '<span class="muted">未填写参数</span>';
}
const collapsedDetailPanels = new Map();
function detailPanelPreferenceKey(expId, panel) {
  return (
    "expvault.web.collapsed-panel:" +
    JSON.stringify([state.session?.root || "", expId, panel])
  );
}
function detailPanelCollapsed(expId, panel) {
  const key = detailPanelPreferenceKey(expId, panel);
  if (!collapsedDetailPanels.has(key)) {
    let collapsed = false;
    try {
      collapsed = localStorage.getItem(key) === "true";
    } catch {}
    collapsedDetailPanels.set(key, collapsed);
  }
  return collapsedDetailPanels.get(key);
}
function detailPanelToggle(panel, label, collapsed, count = "") {
  return `<button type="button" class="text-button panel-collapse-toggle ${collapsed ? "panel-restore-toggle" : ""}" data-action="toggle-detail-panel" data-panel="${panel}" aria-expanded="${!collapsed}" aria-controls="experiment-${panel}-panel" aria-label="${collapsed ? "展开" : "折叠"}${label}"><span class="panel-collapse-chevron" aria-hidden="true">${icon("arrow")}</span>${collapsed ? "展开" + e(label) : "折叠"}${collapsed && count ? `<span class="section-count">${e(count)}</span>` : ""}</button>`;
}
function parameterPanel(meta) {
  const values = orderedParameters(meta),
    pins = pinnedParameters(meta.id);
  if (state.parameterExpId !== meta.id) {
    state.parameterExpId = meta.id;
    state.parametersExpanded = false;
  }
  const shown = state.parametersExpanded ? values : values.slice(0, 6);
  const collapsed = detailPanelCollapsed(meta.id, "parameters");
  return `${collapsed ? detailPanelToggle("parameters", "关键实验参数", true, `${values.length} 项`) : ""}<section id="experiment-parameters-panel" class="panel parameters-panel" ${collapsed ? "hidden" : ""} aria-label="关键实验参数"><header class="panel-header"><div><h2>关键实验参数 <span class="section-count">${values.length}</span></h2><p>点击图钉置顶常看参数，切换页签也能查看。</p></div><div class="toolbar-group">${collapsed ? "" : detailPanelToggle("parameters", "关键实验参数", false)}${values.length > 6 ? `<button type="button" class="text-button" data-action="toggle-parameters" aria-expanded="${!!state.parametersExpanded}">${state.parametersExpanded ? "收起参数" : `查看全部 ${values.length} 项`}</button>` : ""}<button type="button" class="text-button" data-action="edit-parameters">编辑参数</button></div></header><div id="experiment-parameters-content">${shown.length ? `<dl class="parameter-grid">${shown.map(([key, value]) => `<div class="parameter-card ${pins.includes(key) ? "pinned" : ""}"><dt>${scientificHTML(key)}<button type="button" class="icon-button parameter-pin" data-action="pin-parameter" data-param="${e(key)}" aria-pressed="${pins.includes(key)}" aria-label="${pins.includes(key) ? "取消置顶参数" : "置顶参数"} ${e(key)}">${icon("pin")}</button></dt><dd class="${parameterValue(value).length > 16 ? "long-value" : ""}">${scientificHTML(parameterValue(value))}</dd></div>`).join("")}</dl>` : '<div class="parameter-empty">还没有设置工艺或测试参数。<button class="text-button" data-action="edit-parameters">添加参数</button></div>'}</div></section>`;
}
function measurementPanel(meta) {
  const groups = meta.measurement_groups || [];
  const collapsed = detailPanelCollapsed(meta.id, "measurements");
  return `${collapsed ? detailPanelToggle("measurements", "仪器与对照测量", true, `${groups.length} 台仪器`) : ""}<section id="experiment-measurements-panel" class="panel measurements-panel" ${collapsed ? "hidden" : ""} aria-label="仪器与对照测量"><header class="panel-header"><div><h2>仪器与对照测量 <span class="section-count">${groups.length} 台仪器</span></h2><p class="field-hint">同一仪器可多次记录同名参数，按测量组横向对照。</p></div><div class="toolbar-group">${collapsed ? "" : detailPanelToggle("measurements", "仪器与对照测量", false)}${button("add-instrument", "添加仪器", "plus")}</div></header><div id="experiment-measurements-content">${
    groups.length
      ? groups
          .map((group) => {
            const runs = group.runs || [];
            const keys = [
              ...new Set(runs.flatMap((run) => Object.keys(run.params))),
            ];
            return `<article class="measurement-group" data-instrument-id="${e(group.id)}"><header class="measurement-heading"><div><h3>${scientificHTML(group.name)}</h3><p>仪器型号：<strong>${scientificHTML(group.model || "未记录")}</strong> · ${runs.length} 组测量</p></div><div class="toolbar-group">${button("edit-instrument", "编辑仪器", "edit", `data-group="${e(group.id)}"`)}${button("add-measurement", "记录测量", "plus", `data-group="${e(group.id)}"`, true)}</div></header>${runs.length ? `<div class="comparison-scroll"><table class="measurement-table"><caption class="sr-only">${e(group.name)}的对照测量</caption><thead><tr><th scope="col">参数</th>${runs.map((run) => `<th scope="col" data-run-id="${e(run.id)}"><strong>${scientificHTML(run.name)}</strong><small>${e(date(run.measured_at, true))} · ${e(run.operator || "未署名")}</small><div class="toolbar-group"><button type="button" class="text-button" data-action="edit-measurement" data-group="${e(group.id)}" data-run="${e(run.id)}" aria-label="编辑测量 ${e(run.name)}">编辑</button><button type="button" class="text-button" data-action="copy-measurement" data-group="${e(group.id)}" data-run="${e(run.id)}" aria-label="复制测量 ${e(run.name)}">复制为新测量</button></div></th>`).join("")}</tr></thead><tbody>${keys.map((key) => `<tr><th scope="row">${scientificHTML(key)}</th>${runs.map((run) => `<td>${Object.hasOwn(run.params, key) ? scientificHTML(parameterValue(run.params[key])) : '<span class="muted">—</span>'}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="field-hint comparison-hint">每列为一次测量；缺少的参数显示为 —。测量较多时可横向滚动。</p>` : '<p class="prose muted measurement-empty">已记录仪器型号，点击「记录测量」添加第一组参数。</p>'}</article>`;
          })
          .join("")
      : '<p class="prose muted measurement-empty">还没有仪器记录。添加仪器后，可记录初测、复测和不同条件下的参数。</p>'
  }</div></section>`;
}
function experimentGroupChips(names) {
  return (names || [])
    .map(
      (name) =>
        `<button type="button" class="group-chip" data-action="filter-experiment-group" data-group-name="${e(name)}">${scientificHTML(name)}</button>`,
    )
    .join("");
}
function experimentRows(records, selectable = false) {
  return records
    .map(
      (r) =>
        `<tr>${selectable ? `<td class="col-select"><input type="checkbox" data-experiment-check data-id="${e(r.id)}" aria-label="选择实验 ${e(r.title)}" ${state.experimentSelection.has(r.id) ? "checked" : ""}></td>` : ""}<td class="col-experiment"><button class="text-button experiment-title" data-action="open-experiment" data-id="${e(r.id)}">${scientificHTML(r.title)}</button><span class="cell-subtitle">样品 ${scientificHTML(r.device || "未填写")} · ${e(date(r.date))}</span>${r.groups?.length ? `<div class="experiment-group-line">${experimentGroupChips(r.groups)}</div>` : ""}</td><td class="col-status">${badge(r.status)}${statusButton(r)}</td><td class="col-parameters">${compactParameters(r)}</td><td class="col-updated">${e(date(r.updated_at, true))}</td><td class="col-files">${r.file_count || 0} 个<span class="cell-subtitle">${size(r.total_bytes)}</span></td><td class="col-open"><button class="icon-button" data-action="open-experiment" data-id="${e(r.id)}" aria-label="打开 ${scientificHTML(r.title)}">${icon("arrow")}</button></td></tr>`,
    )
    .join("");
}
function experimentTable(records, selectable = false) {
  return `<div class="table-wrap"><table class="experiment-table"><caption class="sr-only">实验项目与关键参数</caption><thead><tr>${selectable ? '<th scope="col" class="col-select"><input type="checkbox" id="select-all-experiments" aria-label="选择本页实验"></th>' : ""}<th scope="col" class="col-experiment">实验 / 样品</th><th scope="col" class="col-status">状态</th><th scope="col" class="col-parameters">关键参数</th><th scope="col" class="col-updated">最近更新</th><th scope="col" class="col-files">实验资料</th><th scope="col" class="col-open"><span class="sr-only">打开实验</span></th></tr></thead><tbody>${experimentRows(records, selectable)}</tbody></table></div>`;
}
function activities(items) {
  return `<div class="activity-list">${
    items
      .slice(0, 5)
      .map(
        (item) =>
          `<div class="activity-item"><span class="activity-symbol">${icon(item.action === "create" ? "plus" : item.action === "backup" ? "shield" : "note")}</span><div class="activity-detail"><strong>${e(eventNames[item.action] || item.action)}</strong><p>${e(item.detail || item.exp_id || "实验库记录")}</p><time>${e(date(item.ts, true))}</time></div></div>`,
      )
      .join("") || '<p class="muted">还没有活动记录</p>'
  }</div>`;
}
async function renderPage({ focusHeading = false } = {}) {
  const generation = ++state.generation;
  const content = $("content");
  const keepContent = !focusHeading && $("page-title");
  $("global-query").value = state.listQuery;
  content.setAttribute("aria-busy", "true");
  if (!keepContent)
    content.innerHTML =
      '<div class="loading"><progress aria-label="读取实验资料"></progress><span>正在读取实验资料…</span></div>';
  const active = state.view === "experiment" ? "experiments" : state.view;
  document
    .querySelectorAll(".nav-item")
    .forEach((el) => {
      const current = el.dataset.nav === active;
      el.classList.toggle("active", current);
      if (current) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  $("breadcrumb-current").textContent = {
    overview: "工作概览",
    experiments: "实验项目",
    experiment: "实验详情",
    files: "文件资料",
    backups: "备份与校验",
    sync: "自动同步",
  }[state.view];
  try {
    let html;
    if (state.view === "overview") html = await overviewPage();
    else if (state.view === "experiments") html = await experimentsPage();
    else if (state.view === "backups") html = await backupsPage();
    else if (state.view === "sync") html = await syncPage();
    else if (state.view === "files") html = await libraryPage();
    else html = await detailPage();
    if (generation !== state.generation) return;
    const scrollTop = window.scrollY;
    content.innerHTML = nasBanner() + html;
    hydratePage();
    applyNASReadOnly();
    const heading = content.querySelector("h1");
    if (heading) {
      heading.id = "page-title";
      heading.tabIndex = -1;
    }
    document.title = `${heading?.textContent || $("breadcrumb-current").textContent} · expvault`;
    if (focusHeading && !$("editor").open)
      heading?.focus({ preventScroll: true });
    else {
      if (scrollTop) window.scrollTo({ top: scrollTop, behavior: "instant" });
      $("page-status").textContent = `已更新${$("breadcrumb-current").textContent}`;
    }
  } catch (error) {
    if (generation !== state.generation) return;
    if (keepContent) toast(errorText(error), true);
    else content.innerHTML = `<div class="error-panel" role="alert"><h2>暂时无法读取资料</h2><p>${e(errorText(error))}</p>${button("reload", "重新读取", "refresh")}</div>`;
  } finally {
    if (generation === state.generation) content.setAttribute("aria-busy", "false");
  }
}
async function overviewPage() {
  const [data, active] = await Promise.all([
    api("overview"),
    api(
      "experiments?" +
        query({ status: "running", limit: 12, offset: state.activePage * 12 }),
    ),
  ]);
  if (state.activePage && !active.items.length) {
    state.activePage = Math.max(0, Math.ceil(active.total / 12) - 1);
    return overviewPage();
  }
  $("nav-count").textContent = data.experiments;
  const cards = [
    ["实验总数", data.experiments, "当前实验库的全部记录", "flask", "all"],
    ["进行中", data.running, "打开实验，继续记录", "activity", "running"],
    ["已完成", data.done, "实验结果与过程可追溯", "check", "done"],
    [
      "实验资料",
      data.files,
      `${size(data.bytes)} · 原始文件保存在本地`,
      "folder",
      "files",
    ],
  ];
  return (
    '<section class="overview-welcome">' +
    pageHeading(
      "LAB WORKSPACE",
      "实验工作台",
      "查看样品、关键条件和最新记录，继续你的实验。",
      button("new-experiment", "新建实验", "plus", "", true),
    ) +
    `<div class="overview-context"><span>${icon("folder")}${scientificHTML(state.session.workspace?.name || state.session.project)}</span><span>${icon("shield")}原始文件保留 · 校验可追溯</span></div></section>` +
    `<div class="stats-grid">${cards.map(([label, value, foot, name, filter]) => `<button type="button" class="stat-card" data-action="overview-filter" data-filter="${filter}" aria-label="查看${label}"><span class="stat-icon">${icon(name)}</span><span class="stat-label">${label}</span><span class="stat-value">${e(value)}</span><span class="stat-foot">${e(foot)}${icon("arrow")}</span></button>`).join("")}</div>
    <section class="active-section" aria-label="进行中的实验"><header class="section-heading"><div><h2>进行中的实验 <span class="section-count">${active.total}</span></h2><p>样品与关键参数速览，可直接修改状态。</p></div><button class="text-button" data-action="show-running">查看全部 ${icon("arrow")}</button></header>
    ${active.items.length ? `<div class="active-grid">${active.items.map((record) => `<article class="active-experiment" data-exp-id="${e(record.id)}"><div class="active-card-meta"><span title="${e(record.device)}">${icon("flask")}样品 ${scientificHTML(record.device || "未填写")}</span><span class="running-indicator" aria-hidden="true"></span></div><h3><button class="text-button" data-action="open-experiment" data-id="${e(record.id)}" title="${e(record.title)}">${scientificHTML(record.title)}</button></h3><div class="active-card-parameters">${compactParameters(record, 2)}</div><footer><span>${e(record.operator || "未填写操作人")} · ${e(date(record.date))}</span>${statusButton(record)}</footer></article>`).join("")}</div><footer class="active-pagination"><span>共 ${active.total} 个 · 第 ${state.activePage + 1} / ${Math.ceil(active.total / 12)} 页 · 每页 12 个</span>${active.total > 12 ? `<div class="toolbar-group"><button class="button small" data-action="active-previous" ${state.activePage === 0 ? "disabled" : ""}>上一页</button><button class="button small" data-action="active-next" ${!active.has_more ? "disabled" : ""}>下一页</button></div>` : ""}</footer>` : `<div class="panel">${empty("当前没有进行中的实验", "可以从实验项目继续已有记录，或新建一项实验。", button("new-experiment", "新建实验", "plus"))}</div>`}</section>
    <div class="dashboard-grid"><section class="panel"><header class="panel-header"><h2>最近的实验</h2><a class="text-button" href="/?view=experiments" data-nav="experiments">查看全部 ${icon("arrow")}</a></header>${data.recent.length ? experimentTable(data.recent) : empty("还没有实验记录", "新建实验后，即可填写参数、记录过程并整理资料。")}</section><section class="panel"><header class="panel-header"><h2>最近记录</h2>${icon("clock")}</header>${activities(data.events)}</section></div>`
  );
}
async function experimentsPage() {
  const groups = await api("groups");
  const groupQuery =
    state.groupFilter === "ungrouped"
      ? { ungrouped: "1" }
      : state.groupFilter.startsWith("group:")
        ? { group: state.groupFilter.slice(6) }
        : {};
  const data = await api(
    "experiments?" +
      query({
        q: state.listQuery,
        status: state.status,
        archived: state.archived ? "1" : undefined,
        offset: state.page * 30,
        limit: 30,
        ...groupQuery,
      }),
  );
  return (
    pageHeading(
      "EXPERIMENTS",
      "实验项目",
      "管理实验目标、样品参数、过程记录和全部资料。",
      button("manage-experiment-groups", "管理分组", "folder") +
        button("new-experiment", "新建实验", "plus", "", true),
    ) +
    `<div class="toolbar"><div class="toolbar-group"><div class="search-box">${icon("search")}<input id="experiment-query" aria-label="筛选实验" value="${e(state.listQuery)}" placeholder="搜索实验、样品或文件标签"></div><select id="status-filter" aria-label="实验状态"><option value="">全部状态</option>${Object.entries(
      statuses,
    )
      .map(
        ([key, value]) =>
          `<option value="${key}" ${state.status === key ? "selected" : ""}>${value}</option>`,
      )
      .join(
        "",
      )}</select><select id="experiment-group-filter" aria-label="实验分组筛选"><option value="">全部分组</option><option value="ungrouped" ${state.groupFilter === "ungrouped" ? "selected" : ""}>未分组</option>${groups.items.map((item) => `<option value="group:${e(item.name)}" ${state.groupFilter === "group:" + item.name ? "selected" : ""}>${e(item.name)}（${item.count}）</option>`).join("")}${groupQuery.group && !groups.items.some((item) => item.name === groupQuery.group) ? `<option value="${e(state.groupFilter)}" selected>${e(groupQuery.group)}（暂无实验）</option>` : ""}</select></div><label class="checkbox-label"><input id="include-archived" type="checkbox" ${state.archived ? "checked" : ""}>包含已归档</label></div><section class="panel"><div class="experiment-selection-bar"><span id="experiment-selection-count">已选 ${state.experimentSelection.size} 个</span><div class="toolbar-group">${button("bulk-add-groups", "添加到分组", "plus", `data-experiment-bulk ${state.experimentSelection.size ? "" : "disabled"}`)}${button("bulk-remove-groups", "移出分组", "folder", `data-experiment-bulk ${state.experimentSelection.size ? "" : "disabled"}`)}${button("clear-experiment-selection", "清空选择", "close", `data-experiment-bulk ${state.experimentSelection.size ? "" : "disabled"}`)}</div></div><p class="experiment-selection-hint field-hint">可跨页选择，最多 200 个；更改筛选会清空选择。分组计数含已归档实验。</p>${data.items.length ? experimentTable(data.items, true) : empty("没有找到匹配的实验", "试试其他关键词，或新建一项实验。")}<footer class="table-footer"><span>共 ${data.total} 个匹配实验 · 第 ${state.page + 1} 页</span><div class="toolbar-group"><button class="button small" data-action="previous-page" ${state.page === 0 ? "disabled" : ""}>上一页</button><button class="button small" data-action="next-page" ${!data.has_more ? "disabled" : ""}>下一页</button></div></footer></section>`
  );
}
async function detailPage() {
  if (!state.expId)
    return empty(
      "先选择一项实验",
      "在实验项目中选择要查看的记录。",
      `<a class="button primary" href="/?view=experiments" data-nav="experiments">查看实验项目</a>`,
    );
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  state.meta = meta;
  const actions =
    `<details class="experiment-reuse"><summary class="button">${icon("copy")}复用实验</summary><div class="reuse-menu">${button("duplicate-experiment", "复制实验", "copy")}${button("batch-experiments", "按模板批量生成", "plus")}</div></details>` +
    button("edit-experiment", "编辑实验", "edit") +
    button("phone-import", "扫码传入", "upload") +
    button("import", "导入资料", "upload", "", true);
  let html = `<div class="page-heading detail-heading"><div><a class="back-link" href="/?view=experiments" data-nav="experiments">${icon("back")}返回实验项目</a><div class="detail-identity"><h1>${scientificHTML(meta.title)}</h1>${badge(meta.status)}${statusButton(meta)}</div><div class="detail-subtitle"><span class="mono">${e(meta.id)}</span><span>${e(date(meta.date))}</span><span>${e(meta.project || "未设置课题")}</span></div>${meta.template_source ? `<p class="field-hint">来源模板：${scientificHTML(meta.template_source.title || "")} · ${e(meta.template_source.id || "")}（创建时编号）</p>` : ""}</div><div class="heading-actions">${actions}</div></div><dl class="experiment-facts">${[
    ["器件 / 样品", meta.device || "未填写样品编号"],
    ["工艺批次", meta.batch || "未填写"],
    ["操作人", meta.operator || "未填写"],
    ["实验日期", date(meta.date)],
  ]
    .map(
      ([label, value]) =>
        `<div><dt>${label}</dt><dd>${scientificHTML(value)}</dd></div>`,
    )
    .join(
      "",
    )}</dl><div class="detail-experiment-groups"><div><span class="muted">实验分组</span><div class="experiment-group-line">${experimentGroupChips(meta.groups) || '<span class="muted">未分组</span>'}</div>${meta.groups_invalid ? '<p class="field-hint">分组数据格式异常，可重新设置分组。</p>' : ""}</div>${button("edit-experiment-groups", "设置分组", "folder")}</div><div id="experiment-parameters">${parameterPanel(meta)}</div><div id="experiment-measurements">${measurementPanel(meta)}</div><div class="tabs" role="tablist" aria-label="实验内容">${[
    ["summary", "实验概览"],
    ["steps", `步骤与日志 · ${(meta.steps || []).length}`],
    ["files", `文件资料 · ${meta.file_count || 0}`],
    ["history", "变更记录"],
  ]
    .map(
      ([key, label]) =>
        `<button id="detail-tab-${key}" class="tab ${state.tab === key ? "active" : ""}" role="tab" aria-selected="${state.tab === key}" aria-controls="detail-panel" tabindex="${state.tab === key ? 0 : -1}" data-action="detail-tab" data-tab="${key}">${e(label)}</button>`,
    )
    .join("")}</div>`;
  const tabPanel = (body) =>
    html +
    `<div id="detail-panel" role="tabpanel" aria-labelledby="detail-tab-${state.tab}" tabindex="0">${body}</div>`;
  if (state.tab === "files") return tabPanel(await filesSection());
  if (state.tab === "steps") return tabPanel(stepsSection(meta));
  if (state.tab === "history")
    return tabPanel(
      `<section class="panel"><header class="panel-header"><h2>实验变更记录</h2><span class="muted">最近 100 条</span></header><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作</th><th>内容</th></tr></thead><tbody>${
        [...(meta.history || [])]
          .reverse()
          .map(
            (item) =>
              `<tr><td>${e(date(item.ts, true))}</td><td>${e(eventNames[item.action] || item.action)}</td><td class="file-name">${e(item.detail)}</td></tr>`,
          )
          .join("") || '<tr><td colspan="3">还没有变更记录</td></tr>'
      }</tbody></table></div></section>`
    );
  const prose = (title, key, name) =>
    `<section class="panel"><div class="panel-body spaced"><h3 class="section-title">${icon(name)}${title}</h3><p class="prose ${meta[key] ? "" : "empty"}">${scientificHTML(meta[key] || "暂未填写，点击「编辑实验」补充。")}</p></div></section>`;
  const latest = (meta.steps || []).at(-1);
  const latestNote = (meta.journal_blocks || [])
    .filter((entry) => entry.kind === "entry")
    .at(-1);
  return tabPanel(
    `<div class="detail-grid"><div class="section-stack">
    <section class="panel findings-panel"><header class="panel-header"><h2>实验结果与下一步</h2>${icon("activity")}</header><div class="findings-grid"><div><h3>结果与观察</h3><p class="prose ${meta.results ? "" : "empty"}">${scientificHTML(meta.results || "尚未记录结果，实验过程中可随时补充。")}</p></div><div><h3>结论与下一步</h3><p class="prose ${meta.conclusion ? "" : "empty"}">${scientificHTML(meta.conclusion || "尚未填写结论与后续计划。")}</p></div></div></section>
    <div class="context-grid">${prose("实验目的", "purpose", "target")}${prose("测试条件", "conditions", "flask")}</div>
    <section class="panel"><div class="panel-body spaced"><h3 class="section-title">${icon("tag")}实验标签与备注</h3><div class="tag-line">${tagHTML(meta.tags) || '<span class="muted">暂无标签</span>'}</div><p class="prose experiment-notes">${notesHTML(meta.notes || "暂无备注")}</p></div></section>
  </div><aside class="detail-sidebar"><section class="panel"><header class="panel-header"><h2>现场记录</h2><span class="section-count">${(meta.steps || []).length} 步</span></header><div class="panel-body"><p class="latest-step-label">最近的实验步骤</p>${latest ? `<strong class="latest-step-title">${scientificHTML(latest.title || `步骤 ${latest.number}`)}</strong><p class="prose latest-step-text">${scientificHTML(latest.text || "暂无文字记录")}</p><p class="field-hint">${e(date(latest.created_at, true))}</p>` : '<p class="prose empty">还没有步骤，先记录本次操作与条件。</p>'}${latest ? referencesHTML(latest.references) : ""}${latestNote ? `<div class="latest-note"><p class="latest-step-label">最近的日志</p><p class="prose latest-step-text">${scientificHTML(latestNote.text)}</p>${referencesHTML(latestNote.references)}</div>` : ""}<div class="record-actions">${button("add-step", "记录步骤", "plus", "", true)}${button("add-note", "追加日志", "note")}</div></div></section>
  <section class="panel"><div class="panel-body spaced"><h3 class="section-title">${icon("shield")}资料与归档</h3><p class="prose">文件 ${meta.file_count || 0} 个 · ${size(meta.total_bytes)}</p><dl class="metadata-list"><dt>创建时间</dt><dd>${e(date(meta.created_at, true))}</dd><dt>最近更新</dt><dd>${e(date(meta.updated_at, true))}</dd><dt>实验类别</dt><dd>${e(meta.category || "未填写")}</dd></dl><div class="heading-actions">${button("verify", "校验文件", "shield")}${button("archive", meta.archived ? "恢复实验" : "归档实验", "archive")}</div></div></section></aside></div>`
  );
}

function stepsSection(meta) {
  const stepsHTML = (meta.steps || [])
    .map(
      (step) =>
        `<article class="step-card"><header class="step-header"><span class="step-number">${String(step.number).padStart(2, "0")}</span><div class="step-title"><h3>${scientificHTML(step.title)}</h3><small>${e(step.author || "未署名")} · ${e(date(step.created_at, true))}</small></div><button class="icon-button" data-action="edit-step" data-number="${step.number}" aria-label="编辑步骤 ${step.number}">${icon("edit")}</button></header><p class="prose">${scientificHTML(step.text)}</p>${referencesHTML(step.references)}${step.images?.length ? `<div class="step-images">${step.images.map((image) => `<button data-action="preview-file" data-path="${e(image.path)}"><img src="${e(fileURL(image.path))}" alt="${e(image.name)}" loading="lazy"><span>${e(image.name)}</span></button>`).join("")}</div>` : ""}</article>`,
    )
    .join("");
  return `<div class="toolbar"><span class="muted">按步骤记录操作、观察与过程图片。</span><div class="toolbar-group">${button("add-note", "追加日志", "note")}${button("add-step", "记录步骤", "plus", "", true)}</div></div><div class="detail-grid"><div class="timeline">${stepsHTML || `<div class="panel">${empty("把实验过程记录下来", "每个步骤都可以包含文字、时间与多张过程图片。", button("add-step", "记录第一步", "plus", "", true))}</div>`}</div><aside class="panel"><header class="panel-header"><h2>实验日志</h2>${icon("note")}</header><div class="panel-body"><div class="journal">${journalHTML(meta)}</div>${meta.journal_truncated ? '<p class="field-hint">此处展示前 256 KB，完整日志可从文件资料下载。</p>' : ""}</div></aside></div>`;
}
async function libraryPage() {
  const records = await api("experiments?limit=200&archived=1");
  if (!records.items.length)
    return (
      pageHeading(
        "FILES & MATERIALS",
        "文件资料",
        "按实验组织图片、视频、数据与文件标签。",
      ) +
      `<section class="panel">${empty("先创建一项实验", "资料将保存在对应实验下，并登记哈希与文件标签。", button("new-experiment", "新建实验", "plus", "", true))}</section>`
    );
  if (!state.expId || !records.items.some((r) => r.id === state.expId))
    state.expId = records.items[0].id;
  state.meta = await api("experiment?" + query({ exp_id: state.expId }));
  return (
    pageHeading(
      "FILES & MATERIALS",
      "文件资料",
      "照片、视频与仪器数据，保留原格式，也能按文件标签快速找到。",
      button("import", "导入资料", "upload", "", true),
    ) +
    `<div class="choice-card">${icon("flask")}<label for="library-experiment">当前实验</label><select id="library-experiment">${records.items.map((r) => `<option value="${e(r.id)}" ${r.id === state.expId ? "selected" : ""}>${e(r.id + " · " + r.title)}</option>`).join("")}</select><button class="text-button" data-action="open-experiment" data-id="${e(state.expId)}">查看实验 ${icon("arrow")}</button></div>${records.has_more ? '<p class="field-hint">此处列出前 200 个实验；其他实验可在实验项目中搜索后进入文件资料。</p>' : ""}<div class="files-section">${await filesSection()}</div>`
  );
}
async function filesSection() {
  const data = await api(
    "files?" +
      query({
        exp_id: state.expId,
        q: state.fileQuery,
        role: state.fileRole,
        offset: state.filePage * 200,
        limit: 200,
      }),
  );
  state.files = data.items;
  state.selected.clear();
  return `<div class="toolbar"><div class="toolbar-group"><div class="search-box">${icon("search")}<input id="file-query" aria-label="筛选文件名或标签" value="${e(state.fileQuery)}" placeholder="按文件名或文件标签查找"></div><select id="file-role" aria-label="文件角色"><option value="">全部资料</option>${state.session.roles.map((r) => `<option value="${e(r.role)}" ${state.fileRole === r.role ? "selected" : ""}>${e(r.label)}</option>`).join("")}</select></div><div class="toolbar-group">${button("scan", "扫描目录", "refresh")}${state.view === "experiment" ? button("import", "导入资料", "upload") : ""}</div></div><div class="toolbar"><span id="selected-count" class="files-toolbar-count">共 ${data.total} 个文件</span><div class="toolbar-group"><button class="button small" id="file-tag-button" data-action="edit-file-tags" disabled>${icon("tag")}批量标签</button><button class="button small danger" id="file-delete-button" data-action="delete-files" disabled>${icon("trash")}删除所选</button></div></div><section class="panel">${data.items.length ? `<div class="table-wrap"><table><thead><tr><th class="table-checkbox"><input id="select-all-files" type="checkbox" aria-label="选择本页所有可用文件"></th><th>文件名称 / 路径</th><th>类型 / 大小</th><th>文件标签</th><th>操作</th></tr></thead><tbody>${data.items.map((f) => `<tr><td class="table-checkbox"><input type="checkbox" data-file-check="${e(f.relpath)}" aria-label="选择 ${e(f.relpath)}" ${f.missing ? "disabled" : ""}></td><td><div class="file-title-wrap"><span class="type-icon">${kindIcon(f.relpath)}</span><button class="text-button file-name" data-action="preview-file" data-path="${e(f.relpath)}">${e(f.relpath)}</button></div></td><td>${e(kind(f.relpath))}<span class="cell-subtitle">${f.missing ? "文件已丢失" : size(f.size)}</span></td><td><div class="tag-line">${tagHTML(f.tags) || '<span class="muted">—</span>'}</div></td><td><div class="table-actions"><button class="icon-button" data-action="preview-file" data-path="${e(f.relpath)}" aria-label="预览 ${e(f.relpath)}">${icon("search")}</button><a class="icon-button" href="${e(fileURL(f.relpath, true))}" download aria-label="下载 ${e(f.relpath)}">${icon("download")}</a></div></td></tr>`).join("")}</tbody></table></div>` : empty("还没有匹配的资料", "可以导入图片、视频或数据文件，也可以扫描直接复制到实验目录中的文件。", button("import", "导入资料", "upload", "", true))}<footer class="table-footer"><span>共 ${data.total} 个文件 · 每页 200 项</span><div class="toolbar-group"><button class="button small" data-action="files-previous" ${state.filePage === 0 ? "disabled" : ""}>上一页</button><button class="button small" data-action="files-next" ${!data.has_more ? "disabled" : ""}>下一页</button></div></footer></section>`;
}
function syncStatusHTML(data) {
  const settings = data.settings,
    status = data.status;
  const mode =
    {
      folder: "共享目录中转",
      webdav: "WebDAV · 坚果云 / 其他服务",
      "lan-host": "局域网直连 · 接收电脑",
      "lan-client": "局域网直连 · 连接另一电脑",
    }[settings.mode] || "尚未配置";
  return `<header class="panel-header"><h2>本机同步状态</h2><span class="badge ${status.running ? "running" : settings.enabled ? "done" : "planned"}">${status.running ? "正在同步" : settings.enabled ? "自动同步已开启" : "同步未开启"}</span></header><div class="panel-body spaced"><dl class="metadata-list"><dt>方式</dt><dd>${e(mode)}</dd><dt>上次成功同步</dt><dd>${e(status.last_sync ? date(status.last_sync, true) : "尚未同步")}</dd><dt>同步范围</dt><dd>所有工作空间的实验资料（包括 PDF、文档与无扩展名文件）、步骤日志和测量记录</dd><dt>自动检查间隔</dt><dd>${settings.interval || 30} 秒（程序运行时）</dd><dt>媒体筛选</dt><dd>${settings.include_photos === false ? "跳过照片" : settings.photo_limit_mb ? `照片超过 ${e(settings.photo_limit_mb)} MB 跳过` : "包含照片，不限大小"} · ${settings.include_videos === false ? "跳过视频" : "包含视频"}</dd><dt>本轮跳过文件</dt><dd>${status.skipped_files || 0} 个</dd></dl>${status.error ? `<p class="sync-error" role="status">${e(status.error)}</p>` : ""}${status.retry_at ? `<p class="field-hint">下次云端重试：${e(date(new Date(status.retry_at * 1000).toISOString(), true))}；限流等待期间「立即同步」不会提前发送请求。</p>` : ""}<p class="field-hint">离线修改会保留在本机，连接恢复后重试。跳过的媒体不会因筛选而删除；历史备份、报告和设备配置各自保存。</p><div class="heading-actions">${button("sync-configure", data.configured ? "修改同步设置" : "配置同步", "edit", "", !data.configured)}${button("sync-run", "立即同步", "refresh", !data.configured || !settings.enabled || status.running ? "disabled" : "", true)}${data.configured ? button(settings.enabled ? "sync-pause" : "sync-resume", settings.enabled ? "暂停同步" : "恢复自动同步", "activity") : ""}${settings.mode === "lan-host" ? button("sync-pairing", "显示配对信息", "link", settings.enabled ? "" : "disabled") : ""}</div></div>`;
}
function syncConflictsHTML(data) {
  const labels = {
    meta: "记录",
    params: "参数",
    notes: "实验备注",
    steps: "步骤",
    journal_entries: "日志",
    measurement_groups: "仪器测量",
    runs: "测量组",
    files: "文件",
    name: "名称",
    model: "仪器型号",
    title: "实验标题",
    status: "实验状态",
    project: "课题",
  };
  return `<header class="panel-header"><h2>待处理冲突 <span class="section-count">${data.pending_count}</span></h2><span class="muted">双方版本保留</span></header>${
    data.conflicts.length
      ? `<div class="sync-conflict-list">${data.conflicts
          .map(
            (item) =>
              `<article class="sync-conflict"><div><strong>${scientificHTML(item.title)}</strong><p>${
                item.path
                  .slice(2)
                  .map((key) => e(labels[key] || key))
                  .join(" / ") || "工作空间信息"
              }</p><small>${item.kind === "file" ? "原文件的两个版本已保存为可见副本，可在文件资料中核对。" : "同一处内容有不同修改，请比较后选择保留版本。"}</small></div>${button("sync-resolve", "比较并处理", "edit", `data-id="${e(item.id)}"`)}</article>`,
          )
          .join(
            "",
          )}</div>${data.more_conflicts ? '<p class="field-hint">先显示前 100 条，处理后继续查看。</p>' : ""}`
      : '<div class="panel-body"><p class="prose muted">没有待处理冲突。不同字段与可确定的修改会自动合并。</p></div>'
  }`;
}
function syncConnectionHTML(data) {
  const info = data.connection || {};
  const states = {
    unconfigured: "尚未配置",
    unverified: "等待检查连接",
    checking: "正在检查连接",
    verified: "上次检查连接正常",
    listening: "本机正在接收连接",
    paused: "同步已暂停",
  };
  const peers = info.peers || [];
  return `<header class="panel-header"><h2>连接主机</h2>${icon("link")}</header><div class="panel-body spaced"><div class="sync-host"><span class="badge ${["verified", "listening"].includes(info.state) ? "done" : "planned"}">${e(states[info.state] || "等待检查连接")}</span><h3>${e(info.hostname || (info.mode === "lan-client" ? "远端同步电脑" : "选择同步主机"))}</h3><p class="mono sync-host-address">${e(info.address || "配置同步后显示主机地址或中转目录")}</p><p class="field-hint">本机：${e(info.local_hostname || "—")}${info.last_seen ? `<br>上次连接检查：${e(date(info.last_seen, true))}` : ""}</p></div>${info.mode === "lan-host" ? `<div class="sync-peer-list"><strong>最近通信的电脑</strong>${peers.length ? peers.map((peer) => `<article><div><strong>${e(peer.hostname)}</strong><p class="mono">${e(peer.address)}</p></div><span>${peer.recent ? "最近有通信" : "等待再次通信"}<small>${e(date(peer.last_seen, true))}</small></span></article>`).join("") : '<p class="field-hint">还没有已认证的电脑连接。显示配对信息后，在另一台电脑选择本机并输入配对码。</p>'}</div>` : ""}<p class="field-hint">直连按每轮通信检查状态，不是持续在线会话。共享目录模式显示中转路径，不推测另一台电脑是否在线。</p><details class="sync-help"><summary>校园网下如何连接</summary><p class="prose">WebDAV：两端填写同一坚果云或其他 WebDAV 目录，通过云端交换资料，无需校园网内互通。共享目录：两台电脑选择同一共享盘或网盘同步目录。直连：一台接收连接，另一台搜索在线主机并输入配对码；两端须互通。校园网隔离时，可使用共享目录中转或允许互通的独立热点。</p></details></div>`;
}
function syncProgressHTML(data) {
  const progress = data.progress || { label: "等待同步" };
  const active = data.status.running || progress.phase === "receiving";
  const value =
    progress.percent == null
      ? active
        ? ""
        : 'value="0"'
      : `value="${progress.percent}"`;
  const count =
    progress.total_files == null
      ? ""
      : `${progress.done_files || 0} / ${progress.total_files} ${["upload", "download", "receiving"].includes(progress.phase) ? "个文件" : "项"}`;
  const bytes =
    progress.bytes_total == null
      ? ""
      : `${size(progress.bytes_done || 0)} / ${size(progress.bytes_total)}`;
  return `<header class="panel-header"><h2>同步进度</h2><span class="muted">${progress.percent == null ? (active ? "正在处理" : "等待下一轮") : progress.phase === "complete" ? "本轮完成" : `当前阶段 ${progress.percent}%`}</span></header><div class="panel-body sync-progress"><div class="sync-progress-heading"><strong role="status">${e(progress.label)}</strong><span>${e([count, bytes].filter(Boolean).join(" · "))}</span></div><progress aria-label="同步进度" max="100" ${value}></progress>${progress.current_file ? `<p class="sync-current-file">${e(progress.current_file)}</p>` : ""}<p class="field-hint">百分比表示当前阶段进度。扫描、比较和校验阶段在总量确定前显示处理中；完成全部合并后显示本轮完成。</p></div>`;
}
const syncLogLevels = { info: "信息", warning: "提醒", error: "失败" };
function syncLogsHTML(logs) {
  return logs.items.length
    ? logs.items
        .map(
          (item) =>
            `<article class="sync-log-row"><time>${e(date(item.time, true))}</time><span class="badge ${item.level === "error" ? "aborted" : item.level === "warning" ? "planned" : "done"}">${e(syncLogLevels[item.level] || item.level)}</span><div><p>${e(item.message)}</p>${item.file ? `<small>${e(item.file)}</small>` : ""}</div></article>`,
        )
        .join("")
    : '<p class="prose muted">尚无同步日志。配置或开始同步后，会记录阶段、文件传输和处理结果。</p>';
}
async function downloadSyncLog() {
  const logs = await api("sync/logs?limit=1000");
  const text = [
    "实验管理系统 · 本机同步日志",
    `保留最近 ${logs.max_entries} 条；本次导出 ${logs.items.length} 条。`,
    "",
    ...logs.items
      .slice()
      .reverse()
      .map(
        (item) =>
          `${item.time}\t${syncLogLevels[item.level] || item.level}\t${item.message}${item.file ? "\t" + item.file : ""}`,
      ),
  ].join("\n");
  const url = URL.createObjectURL(
    new Blob(["\ufeff" + text], { type: "text/plain;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download =
    "实验同步日志-" + new Date().toISOString().slice(0, 10) + ".txt";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function syncPage() {
  const data = await api("sync");
  if (data.nas)
    return (
      pageHeading("NAS", "直接读取 NAS", data.message) +
      '<section class="panel"><div class="panel-body"><p class="prose">各终端读取 NAS 上的同一份实验文件，无需复制成独立副本。请先确认系统共享已连接；其他电脑选择只读方式打开。</p></div></section>'
    );
  state.syncInfo = data;
  return (
    pageHeading(
      "SYNC & COLLABORATION",
      "双向自动同步",
      "共享目录或局域网直连，让两台电脑各自保留完整实验库。",
      button("sync-refresh", "刷新状态", "refresh"),
    ) +
    `<section class="panel sync-progress-panel" id="sync-progress-panel">${syncProgressHTML(data)}</section><div class="sync-layout"><section class="panel" id="sync-status-panel">${syncStatusHTML(data)}</section><section class="panel" id="sync-connection-panel">${syncConnectionHTML(data)}</section></div><section class="panel sync-conflicts-panel" id="sync-conflicts-panel">${syncConflictsHTML(data)}</section><section class="panel sync-log-panel"><header class="panel-header"><h2>同步日志</h2>${button("sync-download-log", "下载同步日志", "download")}</header><p class="sync-log-hint field-hint">仅保存在本机，保留最近 1000 条；页面显示最新 50 条，下载可获取全部保留记录。</p><p class="sync-log-hint sync-error" id="sync-log-error" ${data.logs.error ? "" : "hidden"}>${e(data.logs.error)}</p><div class="panel-body sync-log-list" id="sync-log-list" data-latest="${data.logs.items[0]?.id || 0}">${syncLogsHTML(data.logs)}</div></section>`
  );
}
async function openSyncSettings() {
  const [data, connectivity] = await Promise.all([api("sync"), api("connectivity")]);
  const settings = data.settings;
  const interfaces = [...connectivity.interfaces, { address: "127.0.0.1", name: "仅本机" }];
  let discovered = [];
  const mode = settings.mode || "webdav";
  const modeOptions = [
    ["webdav", "WebDAV：坚果云 / 其他服务"],
    ["folder", "共享目录中转"],
    ["lan-host", "局域网直连：本机接收连接"],
    ["lan-client", "局域网直连：连接另一电脑"],
  ];
  openDialog(
    "配置双向自动同步",
    `<div class="form-grid sync-settings-form"><div class="form-field wide"><label for="sync-mode">同步方式</label><select id="sync-mode" name="mode">${modeOptions.map(([key, label]) => `<option value="${key}" ${mode === key ? "selected" : ""}>${label}</option>`).join("")}</select></div><div id="sync-mode-fields" class="wide"></div>${field("sync_interval", "自动检查间隔（秒）", settings.interval || 30, { type: "number" })}${field("sync_photo_limit", "照片大小上限（MB）", settings.photo_limit_mb || 0, { type: "number", hint: "超过此大小的照片跳过传输；0 表示不限，1 MB = 1024² 字节。" })}<label class="checkbox-label wide"><input type="checkbox" name="include_photos" ${settings.include_photos !== false ? "checked" : ""}>同步照片文件</label><label class="checkbox-label wide"><input type="checkbox" name="include_videos" ${settings.include_videos !== false ? "checked" : ""}>同步视频文件</label><p class="field-hint wide">照片和视频可分别选择。筛选同时作用于上传和下载，不会删除已有文件；关闭照片后，照片大小上限暂不生效。两台电脑分别设置各自的传输范围。</p></div>`,
    async (form) => {
      const values = {
        mode: form.get("mode"),
        interval: Number(form.get("sync_interval")),
        photo_limit_mb: Number(form.get("sync_photo_limit")),
        include_photos: form.has("include_photos"),
        include_videos: form.has("include_videos"),
      };
      if (values.mode === "folder")
        values.folder = String(form.get("sync_folder") || "").trim();
      else if (values.mode === "webdav") {
        values.webdav_url = String(form.get("sync_webdav_url") || "").trim();
        values.webdav_username = String(form.get("sync_webdav_username") || "").trim();
        values.webdav_password = String(form.get("sync_webdav_password") || "");
        values.webdav_folder = String(form.get("sync_webdav_folder") || "").trim();
      } else if (values.mode === "lan-host") {
        values.host = String(form.get("sync_host") || "").trim();
        values.port = Number(form.get("sync_port"));
      } else {
        const raw = String(form.get("sync_pairing") || "").trim();
        const peer = discovered.find((item) => item.url === form.get("sync_peer"));
        const code = String(form.get("sync_pairing_code") || "").trim();
        if (raw) {
          if (code) throw new Error("请只填写配对码或手动配对信息中的一项。");
          try {
            values.pairing = JSON.parse(raw);
          } catch {
            throw new Error(
              "配对信息不是有效的 JSON，请完整复制另一台电脑显示的信息。",
            );
          }
        } else if (code || settings.mode !== "lan-client") {
          if (!peer || !code) throw new Error("请选择在线主机并填写接收电脑显示的配对码。");
          values.discovered_peer = peer;
          values.pairing_code = code;
        }
      }
      await api("sync/configure", {
        method: "POST",
        body: JSON.stringify(values),
      });
      closeDialog(true);
      toast("同步设置已保存，正在准备自动同步");
      await renderPage();
    },
    { wide: true, label: "保存并开启同步", kicker: "覆盖所有工作空间" },
  );
  const searchHosts = async () => {
    const select = $("sync-peer");
    const status = $("sync-search-status");
    const search = $("sync-search");
    search.disabled = true;
    status.textContent = "正在搜索局域网内的同步主机…";
    try {
      const data = await api("sync/action", {
        method: "POST",
        body: JSON.stringify({ action: "discover" }),
      });
      if (!select.isConnected) return;
      const previous = select.value;
      discovered = data.items;
      select.replaceChildren(new Option("请选择在线主机", ""));
      for (const item of discovered)
        select.add(new Option(`${item.hostname || "同步主机"} · ${new URL(item.url).host}${item.proof ? "" : "（等待显示配对码）"}`, item.url));
      select.value = discovered.some((item) => item.url === previous)
        ? previous
        : discovered.length === 1 ? discovered[0].url : "";
      status.textContent = discovered.length
        ? `找到 ${discovered.length} 台在线主机。`
        : "未发现在线主机。请确认接收电脑已开启同步，并与本机处于同一局域网。";
    } catch (error) {
      if (select.isConnected) status.textContent = errorText(error);
    } finally {
      search.disabled = false;
    }
  };
  const fields = () => {
    const selected = $("sync-mode").value;
    $("sync-mode-fields").innerHTML =
      selected === "folder"
        ? field("sync_folder", "共享目录绝对路径", settings.folder || "", {
            required: true,
            wide: true,
            hint: "例如 Windows 的 Z:\\实验同步，Mac 的 /Volumes/共享盘/实验同步；不要选本机实验库内部。",
          })
        : selected === "webdav"
          ? `<div class="form-grid sync-webdav-fields">${field("sync_webdav_url", "WebDAV 服务地址", settings.webdav_url || "https://dav.jianguoyun.com/dav/", { type: "url", required: true, wide: true, hint: "坚果云使用上方默认地址；其他 WebDAV 服务须填写 HTTPS 地址。" })}${field("sync_webdav_username", "WebDAV 账号", settings.webdav_username || "", { required: true, hint: "坚果云填写账号邮箱。" })}${field("sync_webdav_password", "第三方应用密码", "", { type: "password", hint: settings.webdav_password_saved ? "已保存；地址和账号相同时留空沿用。更换账号或地址请重新填写。" : "在坚果云「账户信息 → 安全选项 → 第三方应用管理」生成，请勿填写登录密码。" })}${field("sync_webdav_folder", "云端同步目录", settings.webdav_folder || "ExpVault", { required: true, wide: true, hint: "两台电脑填写同一专用目录，例如 ExpVault 或 科研/实验同步；程序在其中保存同步历史。" })}<p class="field-hint wide">无需安装坚果云客户端。应用密码保存在本机，不进入同步资料或日志。坚果云默认每 300 秒检查，保存连接与首次同步可能需要等待。</p></div>`
        : selected === "lan-host"
          ? `<div class="form-grid"><div class="form-field"><label for="sync-host">本机网卡 IPv4 地址 *</label><select id="sync-host" name="sync_host" required aria-describedby="sync-host-hint"><option value="">请选择网卡</option>${interfaces.map((item) => `<option value="${e(item.address)}" ${item.address === (settings.host || connectivity.interfaces[0]?.address) ? "selected" : ""}>${e(item.name)} · ${e(item.address)}</option>`).join("")}</select><p class="field-hint" id="sync-host-hint">请选择两台电脑可以互通的网卡，例如 Wi-Fi、有线或热点网卡。</p></div>${field("sync_port", "直连服务端口", settings.port || 8766, { type: "number", required: true })}</div>`
          : `<div class="form-grid"><div class="form-field"><label for="sync-peer">在线同步主机</label><select id="sync-peer" name="sync_peer" aria-describedby="sync-search-status"><option value="">请选择在线主机</option></select></div>${field("sync_pairing_code", "一次性配对码", "", { hint: "在接收电脑的同步页面点击「显示配对信息」，输入显示的配对码。" })}<div class="wide"><button type="button" id="sync-search" class="button">${icon("refresh")}重新搜索</button><p class="field-hint" id="sync-search-status" role="status"></p></div></div><details class="sync-help"><summary>手动配对</summary>${field("sync_pairing", "另一台电脑的配对信息", "", {
              type: "textarea",
              wide: true,
              hint:
                settings.mode === "lan-client"
                  ? `当前连接 ${settings.peer_url}。配对码和手动配对均留空时沿用当前连接。`
                  : "支持旧版本电脑，或局域网广播不可用时使用。请复制接收电脑显示的完整配对信息。",
            })}</details>`;
    if (selected === "lan-client") {
      discovered = [];
      $("sync-search").addEventListener("click", searchHosts);
      $("field-sync_pairing_code").autocomplete = "one-time-code";
      searchHosts();
    }
    const interval = $("field-sync_interval");
    interval.min = selected === "webdav" ? 60 : 5;
    if (selected === "webdav" && Number(interval.value) < 60)
      interval.value = 300;
    const password = $("field-sync_webdav_password");
    if (password) password.autocomplete = "new-password";
  };
  $("sync-mode").addEventListener("change", fields);
  fields();
  $("field-sync_interval").max = 3600;
  $("field-sync_photo_limit").min = 0;
  $("field-sync_photo_limit").step = "any";
}
async function openSyncConflict(id) {
  const data = await api("sync");
  const item = data.conflicts.find((value) => value.id === id);
  if (!item) throw new Error("冲突已变化，请刷新同步状态后再处理");
  const choice = (text) => {
    try {
      const value = JSON.parse(text);
      return typeof value === "string"
        ? value
        : JSON.stringify(
            value,
            (key, value) =>
              ["sync_id", "id"].includes(key) ? undefined : value,
            2,
          );
    } catch {
      return text;
    }
  };
  openDialog(
    "比较并处理冲突",
    `<p class="prose">${scientificHTML(item.title)} · ${e(item.path.at(-1))}</p><p class="field-hint">选择实验记录中要使用的版本。同步目录保留历史版本；文件冲突的副本仍可在文件资料中查看。</p><div class="sync-choice-grid">${item.choices.map((value, index) => `<label class="sync-choice"><span><input type="radio" name="choice" value="${index}" required>使用版本${index === 0 ? "一" : "二"}</span><pre>${e(choice(value))}</pre></label>`).join("")}</div><label class="checkbox-label"><input type="radio" name="choice" value="2" required>保留当前最新内容，仅将这条旧冲突标记为已处理</label>`,
    async (form) => {
      await api("sync/action", {
        method: "POST",
        body: JSON.stringify({
          action: "resolve",
          id,
          choice: Number(form.get("choice")),
        }),
      });
      closeDialog(true);
      toast("已提交处理，合并后将自动同步到另一台电脑");
      await renderPage();
    },
    { wide: true, label: "确认处理", kicker: "保留完整历史" },
  );
}
let syncRefreshInFlight = false;
const syncPanelMarkup = new WeakMap();
function updateSyncPanel(id, render, data) {
  const panel = $(id);
  // Polling should retain scroll anchors and any controls the reader is using.
  const markup = render(data);
  if (syncPanelMarkup.get(panel) === markup) return;
  const active = document.activeElement;
  const action = panel.contains(active) && active.dataset.action;
  const helpFocused = active === panel.querySelector("summary");
  const helpOpen = panel.querySelector("details")?.open;
  panel.innerHTML = markup;
  if (helpOpen) panel.querySelector("details")?.setAttribute("open", "");
  if (helpFocused) panel.querySelector("summary")?.focus({ preventScroll: true });
  if (action)
    [...panel.querySelectorAll("[data-action]")].find((node) =>
      node.dataset.action === action && node.dataset.id === active.dataset.id
    )?.focus({ preventScroll: true });
  syncPanelMarkup.set(panel, markup);
}
async function refreshSync() {
  if (syncRefreshInFlight) return;
  syncRefreshInFlight = true;
  try {
    const data = await api("sync");
    if (data.nas) return;
    state.syncInfo = data;
    const count = $("sync-pending-count");
    if (count) {
      count.textContent = data.pending_count;
      count.hidden = !data.pending_count;
    }
    if (state.view === "sync" && !state.dialog && $("sync-status-panel")) {
      updateSyncPanel("sync-status-panel", syncStatusHTML, data);
      updateSyncPanel("sync-conflicts-panel", syncConflictsHTML, data);
      updateSyncPanel("sync-connection-panel", syncConnectionHTML, data);
      updateSyncPanel("sync-progress-panel", syncProgressHTML, data);
      const logs = $("sync-log-list");
      const latest = String(data.logs.items[0]?.id || 0);
      const selection = window.getSelection();
      if (
        logs.dataset.latest !== latest &&
        !(
          selection &&
          !selection.isCollapsed &&
          logs.contains(selection.anchorNode)
        )
      ) {
        const previousTop = logs.scrollTop,
          previousHeight = logs.scrollHeight;
        logs.innerHTML = syncLogsHTML(data.logs);
        logs.dataset.latest = latest;
        if (previousTop > 30)
          logs.scrollTop = previousTop + logs.scrollHeight - previousHeight;
      }
      $("sync-log-error").textContent = data.logs.error;
      $("sync-log-error").hidden = !data.logs.error;
    }
  } finally {
    syncRefreshInFlight = false;
  }
}
let syncTick = 0;
setInterval(() => {
  syncTick++;
  if (
    state.session &&
    !document.hidden &&
    ["127.0.0.1", "localhost"].includes(location.hostname) &&
    (state.view === "sync" || syncTick % 3 === 0)
  )
    refreshSync().catch(() => {});
}, 1000);

async function backupsPage() {
  const [data, records] = await Promise.all([
    api("backups"),
    api("experiments?limit=200"),
  ]);
  return (
    pageHeading(
      "BACKUPS & INTEGRITY",
      "为实验数据，多留一份保障。",
      "创建增量快照，检查原始文件是否完整，并导出实验台账。",
      button("export-html", "导出 HTML 看板", "export") +
        button("export-xlsx", "导出台账", "download"),
    ) +
    `<div class="alert">${icon("info")}备份只使用已配置的目标，不会自动删除旧快照。移动硬盘未连接时，请先接入设备。</div><div class="backup-grid">${data.targets.map((target) => `<article class="backup-target"><h3>${icon("shield")}${e(target.name)}</h3><p>${e(target.path)}</p><div class="target-footer"><span class="badge ${target.enabled && target.available ? "done" : "planned"}">${!target.enabled ? "未启用" : target.available ? "可用" : "未连接"}</span><button class="button small primary" data-action="backup" data-target="${e(target.name)}" ${!target.enabled || !target.available ? "disabled" : ""}>${icon("plus")}创建备份</button></div></article>`).join("") || '<section class="panel">' + empty("还没有配置备份目标", "在实验库 config.json 中设置 backup.targets 后，可在此创建快照。") + "</section>"}</div><section class="panel"><header class="panel-header"><h2>实验完整性检查</h2>${icon("shield")}</header><div class="panel-body"><p class="prose">逐文件重新计算 SHA256，检查文件改动、丢失和未登记资料。</p><div class="choice-card"><select id="verify-experiment" aria-label="选择待校验实验">${records.items.map((r) => `<option value="${e(r.id)}">${e(r.id + " · " + r.title)}</option>`).join("")}</select><button class="button primary" data-action="verify-selected" ${!records.items.length ? "disabled" : ""}>开始校验</button></div></div></section><section class="panel backup-history"><header class="panel-header"><h2>最近的备份</h2><span class="muted">最多 100 条记录</span></header>${data.history.length ? `<div class="table-wrap"><table><thead><tr><th>创建时间</th><th>备份目标</th><th>快照编号</th><th>文件 / 数据量</th><th>结果</th></tr></thead><tbody>${data.history.map((item) => `<tr><td>${e(date(item.ts, true))}</td><td>${e(item.target)}</td><td class="mono">${e(item.snapshot)}</td><td>${item.files || 0} 个<span class="cell-subtitle">${size(item.bytes_total)}</span></td><td><span class="badge ${item.ok ? "done" : "aborted"}">${item.ok ? "已完成" : "部分失败"}</span></td></tr>`).join("")}</tbody></table></div>` : empty("给实验库创建第一份备份", "选择上方可用目标，保存当前数据的快照。")}</section>`
  );
}

let searchTimer;
function clearExperimentSelection() {
  state.experimentSelection.clear();
  state.experimentLabels.clear();
  document
    .querySelectorAll("[data-experiment-check]")
    .forEach((input) => (input.checked = false));
  updateExperimentSelection();
}
function updateExperimentSelection() {
  const inputs = [...document.querySelectorAll("[data-experiment-check]")];
  for (const input of inputs)
    if (input.checked)
      state.experimentLabels.set(
        input.dataset.id,
        input.closest("tr").querySelector(".experiment-title").textContent,
      );
  const selected = inputs.filter((input) => input.checked).length;
  const all = $("select-all-experiments");
  if (all) {
    all.checked = inputs.length > 0 && selected === inputs.length;
    all.indeterminate = selected > 0 && selected < inputs.length;
  }
  const count = $("experiment-selection-count");
  if (count) count.textContent = `已选 ${state.experimentSelection.size} 个`;
  document
    .querySelectorAll("[data-experiment-bulk]")
    .forEach(
      (button) =>
        (button.disabled = nasReadOnly() || !state.experimentSelection.size),
    );
}
function experimentGroupsField(names = []) {
  return field("groups", "实验分组", (names || []).join(", "), {
    wide: true,
    hint: "逗号分隔，可加入多个组；填写新名称即可建组，留空表示未分组。",
  });
}
async function openExperimentGroups() {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  openDialog(
    "设置实验分组",
    `<p class="prose">${scientificHTML(meta.title)}</p>${experimentGroupsField(meta.groups)}<p class="field-hint">分组仅在当前工作空间内组织实验，不移动或复制文件。</p>`,
    async (form) => {
      const dialog = state.dialog;
      await runAction("experiments.update", {
        exp_id: meta.id,
        expected_revision: meta.revision,
        changes: { groups: tags(form.get("groups")) },
      });
      if (state.dialog === dialog) {
        closeDialog(true);
        await renderPage();
      }
      toast("实验分组已保存");
    },
    { wide: true, label: "保存分组", kicker: meta.id },
  );
  state.dialog.freezeOnSubmit = true;
}
async function openBulkGroups(mode) {
  const ids = [...state.experimentSelection];
  if (!ids.length) throw new Error("请先勾选要编组的实验");
  openDialog(
    mode === "add" ? "添加到实验分组" : "移出实验分组",
    `<p class="prose">已选 ${ids.length} 个实验。</p><ul class="group-selection-preview">${ids
      .slice(0, 5)
      .map((id) => `<li>${e(state.experimentLabels.get(id) || id)}</li>`)
      .join(
        "",
      )}${ids.length > 5 ? `<li>以及其他 ${ids.length - 5} 个实验</li>` : ""}</ul>${experimentGroupsField()}<p class="field-hint">${mode === "add" ? "可填写已有或新分组名称；原有其他分组保留。" : "只移除填写的分组关系；实验、资料与其他分组保留。"}</p>`,
    async (form) => {
      const dialog = state.dialog;
      const names = tags(form.get("groups"));
      if (!names.length) throw new Error("请填写至少一个分组");
      const result = await runAction("experiments.groups", {
        exp_ids: ids,
        groups: names,
        mode,
      });
      state.experimentSelection.clear();
      state.experimentLabels.clear();
      if (state.dialog === dialog) {
        closeDialog(true);
        await renderPage();
      }
      toast(`已更新 ${result.updated} 个实验的分组`);
    },
    {
      wide: true,
      label: mode === "add" ? "确认添加" : "确认移出",
      kicker: "当前工作空间",
    },
  );
  state.dialog.freezeOnSubmit = true;
}
async function openGroupManager() {
  const data = await api("groups");
  openDialog(
    "管理实验分组",
    `<p class="field-hint">一个实验可加入多个分组，计数包含已归档实验。移出最后一个成员后，该分组不再列出。</p>${data.items.length ? `<div class="table-wrap group-manager-table"><table><thead><tr><th>分组</th><th>实验数量</th><th>操作</th></tr></thead><tbody>${data.items.map((item) => `<tr><td><button type="button" class="text-button" data-action="filter-experiment-group" data-group-name="${e(item.name)}">${scientificHTML(item.name)}</button></td><td>${item.count} 个<small class="cell-subtitle">进行中 ${item.running} 个</small></td><td><div class="toolbar-group">${button("rename-experiment-group", "重命名", "edit", `data-group-name="${e(item.name)}"`)}${button("dissolve-experiment-group", "解散分组", "close", `data-group-name="${e(item.name)}"`)}</div></td></tr>`).join("")}</tbody></table></div>` : '<p class="prose">还没有分组。先勾选实验后「添加到分组」，或从实验详情「设置分组」。</p>'}`,
    null,
    { wide: true, kicker: "当前工作空间" },
  );
}
async function renameExperimentGroup(name) {
  openDialog(
    "重命名实验分组",
    `<p class="prose">当前名称：${scientificHTML(name)}</p>${field("new_group_name", "新分组名称", name, { required: true, wide: true, hint: "名称最多 80 字。若新名称已存在，将合并到该组；实验和文件保留。" })}`,
    async (form) => {
      const dialog = state.dialog;
      const result = await runAction("groups.rename", {
        name,
        new_name: String(form.get("new_group_name")).trim(),
      });
      if (state.groupFilter === "group:" + name)
        state.groupFilter = "group:" + result.name;
      state.experimentSelection.clear();
      state.experimentLabels.clear();
      if (state.dialog === dialog) {
        closeDialog(true);
        await renderPage();
        await openGroupManager();
      }
      toast("分组名称已更新");
    },
    { wide: true, label: "保存名称" },
  );
  state.dialog.freezeOnSubmit = true;
}
function hydratePage() {
  document.querySelector('[role="tablist"]')?.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const tabs = [...event.currentTarget.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(event.target);
    if (index < 0) return;
    const next = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    tabs.forEach((tab, position) => (tab.tabIndex = position === next ? 0 : -1));
    tabs[next].focus();
  });
  const bindSearch = (id, update) => {
    const field = $(id);
    if (field)
      field.addEventListener("input", () => {
        if (id === "experiment-query") clearExperimentSelection();
        const value = field.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
          update(value);
          renderPage().then(() => {
            const fresh = $(id);
            if (fresh) {
              fresh.focus();
              fresh.setSelectionRange(value.length, value.length);
            }
          });
        }, 350);
      });
  };
  bindSearch("experiment-query", (value) => {
    state.listQuery = value;
    state.page = 0;
  });
  bindSearch("file-query", (value) => {
    state.fileQuery = value;
    state.filePage = 0;
  });
  $("status-filter")?.addEventListener("change", (event) => {
    clearExperimentSelection();
    state.status = event.target.value;
    state.page = 0;
    renderPage();
  });
  $("include-archived")?.addEventListener("change", (event) => {
    clearExperimentSelection();
    state.archived = event.target.checked;
    state.page = 0;
    renderPage();
  });
  $("experiment-group-filter")?.addEventListener("change", (event) => {
    clearExperimentSelection();
    state.groupFilter = event.target.value;
    state.page = 0;
    renderPage();
  });
  document.querySelectorAll("[data-experiment-check]").forEach((input) =>
    input.addEventListener("change", () => {
      if (input.checked) {
        if (state.experimentSelection.size >= 200) {
          input.checked = false;
          toast("每次最多选择 200 个实验，请分批编组", true);
          return;
        }
        state.experimentSelection.add(input.dataset.id);
      } else {
        state.experimentSelection.delete(input.dataset.id);
        state.experimentLabels.delete(input.dataset.id);
      }
      updateExperimentSelection();
    }),
  );
  $("select-all-experiments")?.addEventListener("change", (event) => {
    const inputs = [...document.querySelectorAll("[data-experiment-check]")];
    const next = new Set(state.experimentSelection);
    inputs.forEach((input) =>
      event.target.checked
        ? next.add(input.dataset.id)
        : next.delete(input.dataset.id),
    );
    if (next.size > 200) {
      toast("每次最多选择 200 个实验，请分批编组", true);
      updateExperimentSelection();
      return;
    }
    state.experimentSelection = next;
    inputs.forEach((input) => (input.checked = event.target.checked));
    updateExperimentSelection();
  });
  updateExperimentSelection();
  $("file-role")?.addEventListener("change", (event) => {
    state.fileRole = event.target.value;
    state.filePage = 0;
    renderPage();
  });
  $("library-experiment")?.addEventListener("change", (event) =>
    navigate("files", event.target.value),
  );
  document.querySelectorAll("[data-file-check]").forEach((input) =>
    input.addEventListener("change", () => {
      input.checked
        ? state.selected.add(input.dataset.fileCheck)
        : state.selected.delete(input.dataset.fileCheck);
      updateSelection();
    }),
  );
  $("select-all-files")?.addEventListener("change", (event) => {
    document
      .querySelectorAll("[data-file-check]:not(:disabled)")
      .forEach((input) => {
        input.checked = event.target.checked;
        input.checked
          ? state.selected.add(input.dataset.fileCheck)
          : state.selected.delete(input.dataset.fileCheck);
      });
    updateSelection();
  });
  document.querySelectorAll(".step-images img").forEach((image) =>
    image.addEventListener("error", () => {
      image.hidden = true;
    }),
  );
}
function updateSelection() {
  if ($("selected-count"))
    $("selected-count").textContent = state.selected.size
      ? `已选择 ${state.selected.size} 个文件`
      : `本页 ${state.files.length} 个文件`;
  $("file-tag-button").disabled = nasReadOnly() || !state.selected.size;
  $("file-delete-button").disabled = nasReadOnly() || !state.selected.size;
}

function openDialog(
  title,
  html,
  submit,
  { wide = false, kicker = "实验管理", label = "保存" } = {},
) {
  closeMenu();
  state.dialog?.cleanup?.();
  if ($("editor").open) $("editor").close();
  state.dialog = { submit, dirty: false };
  $("dialog-form").reset();
  $("dialog-title").textContent = title;
  $("dialog-kicker").textContent = kicker;
  $("dialog-content").innerHTML = html;
  $("dialog-error").hidden = true;
  $("dialog-submit").hidden = !submit;
  $("dialog-submit").textContent = label;
  $("dialog-submit").className = "button primary";
  $("dialog-submit").disabled = false;
  $("editor").classList.toggle("wide", wide);
  setupScientificInputs();
  $("editor").showModal();
  $("dialog-title").focus({ preventScroll: true });
}
function closeDialog(force = false) {
  if (!force && state.dialog?.dirty && !confirm("还有未保存的内容，确定关闭？"))
    return;
  if (state.uploadXHR) {
    toast("请先等待当前文件发送完成，或点击停止上传。", true);
    return;
  }
  state.dialog?.cleanup?.();
  $("editor").close();
  state.dialog = null;
  $("dialog-content").replaceChildren();
}
$("editor").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDialog();
});
$("dialog-form").addEventListener("input", (event) => {
  if (event.target.matches("[data-theme-select], [data-picker-filter]")) return;
  if (state.dialog) state.dialog.dirty = true;
});
$("dialog-form").addEventListener("change", (event) => {
  if (event.target.matches("[data-theme-select], [data-picker-filter]")) return;
  if (state.dialog) state.dialog.dirty = true;
});
window.addEventListener("beforeunload", (event) => {
  if (state.dialog?.dirty || state.uploadXHR) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("dialog-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const dialog = state.dialog;
  const submit = dialog?.submit;
  if (!submit) return;
  const control = $("dialog-submit"),
    label = control.textContent;
  control.disabled = true;
  control.textContent = "正在处理…";
  $("dialog-error").hidden = true;
  const submitted = new FormData($("dialog-form"));
  const frozen = dialog.freezeOnSubmit
    ? [
        ...$("dialog-content").querySelectorAll("input,select,textarea,button"),
      ].map((node) => [node, node.disabled])
    : [];
  frozen.forEach(([node]) => (node.disabled = true));
  try {
    await submit(submitted);
  } catch (error) {
    if (state.dialog === dialog) {
      $("dialog-error").textContent = errorText(error);
      $("dialog-error").hidden = false;
    } else toast(errorText(error), true);
  } finally {
    if (state.dialog === dialog) {
      frozen.forEach(([node, disabled]) => (node.disabled = disabled));
      control.disabled = false;
      control.textContent = label;
    }
  }
});
function field(
  name,
  label,
  value = "",
  { type = "text", wide = false, required = false, hint = "", rows = 3 } = {},
) {
  const scientific = [
    "title",
    "workspace_name",
    "workspace_project",
    "instrument_name",
    "instrument_model",
    "measurement_name",
    "device",
    "purpose",
    "conditions",
    "results",
    "conclusion",
    "notes",
    "text",
  ].includes(name)
    ? "data-scientific"
    : "";
  return `<div class="form-field ${wide ? "wide" : ""}"><label for="field-${name}">${label}${required ? " *" : ""}</label>${type === "textarea" ? `<textarea ${scientific} ${name === "notes" ? "data-rich-notes" : ""} id="field-${name}" name="${name}" rows="${rows}">${e(value)}</textarea>` : `<input ${scientific} id="field-${name}" name="${name}" type="${type}" value="${e(value)}" ${required ? "required" : ""} ${name === "title" ? 'maxlength="256"' : ""}>`}${hint ? `<p class="field-hint">${e(hint)}</p>` : ""}</div>`;
}
function parameterRow(key = "", value = "") {
  return `<div class="parameter-row"><input data-scientific data-param-key aria-label="参数名称" placeholder="参数名称，如温度" value="${e(key)}"><input data-scientific data-param-value aria-label="参数值" placeholder="参数值，如 350 °C" value="${e(value)}"><button type="button" class="icon-button" data-action="remove-parameter" aria-label="删除参数">${icon("close")}</button></div>`;
}
function collectParameters(original = {}) {
  const values = Object.create(null);
  document.querySelectorAll("#parameter-rows .parameter-row").forEach((row) => {
    const key = row.querySelector("[data-param-key]").value.trim();
    const value = row.querySelector("[data-param-value]").value;
    if (!key && value) throw new Error("填写了参数值的行还需要填写名称");
    if (key) {
      if (Object.hasOwn(values, key))
        throw new Error(
          "同一组内参数名称重复：" + key + "；重复测量请新增一组测量。",
        );
      values[key] =
        Object.hasOwn(original, key) && String(original[key] ?? "") === value
          ? original[key]
          : value;
    }
  });
  return values;
}
async function saveMeasurements(meta, groups, dialog) {
  await runAction("experiments.update", {
    exp_id: meta.id,
    expected_revision: meta.revision,
    changes: { measurement_groups: groups },
  });
  if (state.dialog === dialog) closeDialog(true);
  toast("仪器与测量记录已保存");
  await renderPage();
}
async function openInstrumentEditor(groupId = "") {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const groups = structuredClone(meta.measurement_groups || []);
  const group = groups.find((item) => item.id === groupId);
  if (groupId && !group) throw new Error("仪器记录不存在，请刷新后重试");
  openDialog(
    group ? "编辑仪器" : "添加仪器",
    `<div class="form-grid">${field("instrument_name", "仪器名称 / 编号", group?.name || "", { required: true, wide: true, hint: "例如：源表 01、拉曼光谱仪。相同型号的不同仪器可用编号区分。" })}${field("instrument_model", "仪器型号", group?.model || "", { wide: true, hint: "例如：Keithley 2400、Renishaw inVia。" })}</div>`,
    async (form) => {
      const item = {
        id: group?.id || uuid(),
        name: String(form.get("instrument_name")).trim(),
        model: String(form.get("instrument_model") || "").trim(),
        runs: group?.runs || [],
      };
      if (!item.name) throw new Error("请填写仪器名称或编号");
      const next = group
        ? groups.map((value) => (value.id === group.id ? item : value))
        : [...groups, item];
      await saveMeasurements(meta, next, state.dialog);
    },
    { label: "保存仪器", kicker: meta.id },
  );
}
async function openMeasurementEditor(groupId, runId = "", copy = false) {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const groups = structuredClone(meta.measurement_groups || []);
  const group = groups.find((item) => item.id === groupId);
  const previous = group?.runs.find((item) => item.id === runId);
  if (!group || (runId && !previous))
    throw new Error("仪器或测量记录不存在，请刷新后重试");
  const measured =
    previous && !copy ? new Date(previous.measured_at) : new Date();
  const localTime = new Date(
    measured.getTime() - measured.getTimezoneOffset() * 60000,
  )
    .toISOString()
    .slice(0, 16);
  const original = previous?.params || {};
  openDialog(
    previous && !copy ? "编辑测量" : "记录测量",
    `<p class="prose">${scientificHTML(group.name)} · 型号 ${scientificHTML(group.model || "未记录")}</p><div class="form-grid measurement-form">${field("measurement_name", "测量组名称", previous ? previous.name + (copy ? "（复测）" : "") : `第 ${group.runs.length + 1} 次测量`, { required: true, wide: true, hint: "例如：退火前、退火后、对照组、第二次复测。" })}${field("measurement_time", "测量时间", localTime, { type: "datetime-local", required: true })}${field("measurement_operator", "测量操作人", previous && !copy ? previous.operator : state.session.operator)}</div><div class="form-divider">本次测量参数</div><div id="parameter-rows">${
      Object.entries(original)
        .map(([key, value]) => parameterRow(key, value))
        .join("") || parameterRow()
    }</div><button type="button" class="button small" data-action="add-parameter">${icon("plus")}添加参数</button>`,
    async (form) => {
      const params = collectParameters(original);
      if (!Object.keys(params).length)
        throw new Error("请至少记录一项测量参数");
      const item = {
        id: previous && !copy ? previous.id : uuid(),
        name: String(form.get("measurement_name")).trim(),
        measured_at: new Date(
          String(form.get("measurement_time")),
        ).toISOString(),
        operator: String(form.get("measurement_operator") || "").trim(),
        params,
      };
      if (!item.name) throw new Error("请填写测量组名称");
      const runs =
        previous && !copy
          ? group.runs.map((value) => (value.id === previous.id ? item : value))
          : [...group.runs, item];
      const next = groups.map((value) =>
        value.id === group.id ? { ...value, runs } : value,
      );
      await saveMeasurements(meta, next, state.dialog);
    },
    { wide: true, label: "保存测量", kicker: meta.id },
  );
}
function statusButton(record) {
  return record.archived || record.status === "archived"
    ? ""
    : `<button type="button" class="text-button status-edit" data-action="edit-status" data-id="${e(record.id)}">修改状态</button>`;
}
async function openStatusEditor(expId) {
  const meta = await api("experiment?" + query({ exp_id: expId }));
  if (meta.archived) throw new Error("实验已归档，请先恢复实验再修改状态。");
  openDialog(
    "修改实验状态",
    `<p class="prose">${scientificHTML(meta.title)}</p><div class="form-field status-form"><label for="field-status">实验状态</label><select id="field-status" name="status">${Object.entries(
      statuses,
    )
      .filter(([key]) => key !== "archived")
      .map(
        ([key, label]) =>
          `<option value="${key}" ${meta.status === key ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select><p class="field-hint">保存后更新实验列表和首页统计，保留实验资料与记录。</p></div>`,
    async (form) => {
      const dialog = state.dialog;
      const status = form.get("status");
      if (status !== meta.status) {
        await runAction("experiments.update", {
          exp_id: meta.id,
          expected_revision: meta.revision,
          changes: { status },
        });
      }
      if (state.dialog === dialog) closeDialog(true);
      toast(`实验状态已保存为「${statuses[status]}」`);
      await renderPage();
    },
    { label: "保存状态", kicker: meta.id },
  );
}
function templateDate() {
  const value = new Date();
  return [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, "0"),
    String(value.getDate()).padStart(2, "0"),
  ].join("-");
}
function templateOptions() {
  return '<label class="checkbox-label wide"><input type="checkbox" name="copy_instruments" checked>保留仪器名称和型号，不带测量记录</label><label class="checkbox-label wide"><input type="checkbox" name="copy_notes">同时复制原实验备注</label>';
}
async function openDuplicateExperiment() {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  openDialog(
    "复制实验",
    `<p class="prose">以 ${scientificHTML(meta.title)} 为模板，创建独立编号的新实验。</p><p class="field-hint">继承基本信息、目的、条件和参数；不复制结果、结论、原始文件、步骤或日志。</p><div class="form-grid">${field("title", "新实验标题", Array.from(meta.title).slice(0, 252).join("") + "（副本）", { wide: true, required: true })}${field("date", "实验日期", templateDate(), { type: "date", required: true })}<div class="form-field"><label for="clone-status">新实验状态</label><select id="clone-status" name="status"><option value="planned">计划中</option><option value="running">进行中</option></select></div>${field("device", "器件 / 样品编号", meta.device)}${field("batch", "工艺批次", meta.batch)}${field("operator", "操作人", state.session.operator || meta.operator)}${field("conditions", "测试条件", meta.conditions, { type: "textarea", wide: true })}${experimentGroupsField(meta.groups)}${templateOptions()}</div><div class="form-divider">新实验参数</div><div id="parameter-rows">${
      Object.entries(meta.params || {})
        .map(([key, value]) => parameterRow(key, value))
        .join("") || parameterRow()
    }</div><button type="button" class="button small" data-action="add-parameter">${icon("plus")}添加参数</button>`,
    async (form) => {
      const dialog = state.dialog;
      const result = await runAction("experiments.duplicate", {
        exp_id: meta.id,
        expected_revision: meta.revision,
        copy_instruments: form.has("copy_instruments"),
        copy_notes: form.has("copy_notes"),
        changes: {
          title: String(form.get("title")).trim(),
          date: form.get("date"),
          status: form.get("status"),
          device: form.get("device"),
          batch: form.get("batch"),
          operator: form.get("operator"),
          conditions: form.get("conditions"),
          groups: tags(form.get("groups")),
          params: collectParameters(meta.params || {}),
        },
      });
      if (state.dialog !== dialog) {
        toast("实验副本已创建，可从任务面板查看结果");
        return;
      }
      closeDialog(true);
      toast("已创建独立实验，原实验保留");
      navigate("experiment", result.id);
    },
    { wide: true, label: "创建副本", kicker: meta.id },
  );
  state.dialog.freezeOnSubmit = true;
}
function parseTemplateTable(text) {
  const delimiter = text.split(/\r?\n/, 1)[0].includes("\t") ? "\t" : ",";
  const rows = [];
  let row = [],
    cell = "",
    quoted = false,
    closed = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        cell += '"';
        index++;
      } else if (char === '"') {
        quoted = false;
        closed = true;
      } else cell += char;
    } else if (char === '"') {
      if (cell || closed)
        throw new Error(
          "表格引号格式不正确，请使用 Excel 复制的表格或标准 CSV。",
        );
      quoted = true;
    } else if (char === delimiter || char === "\n" || char === "\r") {
      row.push(cell);
      cell = "";
      closed = false;
      if (char !== delimiter) {
        if (row.some((value) => value.trim())) rows.push(row);
        row = [];
        if (char === "\r" && text[index + 1] === "\n") index++;
      }
    } else {
      if (closed && char.trim()) throw new Error("引号结束后只能是分隔符。");
      if (!closed) cell += char;
    }
  }
  if (quoted) throw new Error("表格中有未闭合的引号。");
  row.push(cell);
  if (row.some((value) => value.trim())) rows.push(row);
  if (rows.length < 2)
    throw new Error("请填写首行列名，以及至少一行实验条件。");
  const headers = rows.shift().map((value) => value.trim());
  if (
    headers.some((value) => !value) ||
    new Set(headers).size !== headers.length
  )
    throw new Error("表格列名不能为空或重复。");
  if (rows.some((value) => value.length !== headers.length))
    throw new Error("每行列数须与首行一致；Excel 表格可直接复制粘贴。");
  if (rows.length > 100)
    throw new Error("每次最多生成 100 个实验，请分批处理。");
  const fields = {
    标题: "title",
    实验标题: "title",
    样品: "device",
    样品编号: "device",
    器件: "device",
    条件: "conditions",
    测试条件: "conditions",
    批次: "batch",
    操作人: "operator",
    课题: "project",
    类别: "category",
    目的: "purpose",
    备注: "notes",
    日期: "date",
    状态: "status",
    分组: "groups",
    实验分组: "groups",
  };
  const reserved = new Set([
    "title",
    "device",
    "conditions",
    "batch",
    "operator",
    "project",
    "category",
    "purpose",
    "notes",
    "date",
    "status",
    "groups",
  ]);
  const keys = headers.map(
    (header) =>
      (Object.hasOwn(fields, header) ? fields[header] : null) ||
      (reserved.has(header)
        ? header
        : "params:" + header.replace(/^参数[:：]/, "")),
  );
  if (new Set(keys).size !== keys.length)
    throw new Error("列名对应了同一字段，请删除重复的标题、样品或参数列。");
  return rows.map((cells) => {
    const value = { params: Object.create(null) };
    cells.forEach((cell, index) => {
      cell = cell.trim();
      if (!cell) return;
      const key = keys[index];
      if (key.startsWith("params:")) {
        const name = key.slice(7);
        if (!name || name.length > 128)
          throw new Error("参数名称须为 1–128 字。");
        value.params[name] = cell;
      } else
        value[key] =
          key === "status"
            ? { 计划中: "planned", 进行中: "running" }[cell] || cell
            : key === "groups"
              ? tags(cell)
              : cell;
    });
    return value;
  });
}
function templateTitle(pattern, meta, variant, index) {
  const values = {
    ...meta.params,
    ...variant.params,
    模板标题: meta.title,
    序号: String(index + 1).padStart(2, "0"),
    样品: variant.device ?? meta.device,
    分组: (variant.groups ?? meta.groups ?? []).join("、"),
  };
  return pattern
    .replace(/\{([^{}]+)\}/g, (match, key, offset) => {
      if (Object.hasOwn(values, key)) return String(values[key] ?? "");
      if (offset && ["_", "^"].includes(pattern[offset - 1])) return match;
      throw new Error("标题格式包含未知变量：" + key);
    })
    .trim();
}
function templateAxisRow(name = "", values = "") {
  return `<div class="template-axis-row"><label>变化参数名<input type="text" data-axis-name value="${e(name)}" placeholder="如：退火温度"></label><label>参数取值（每行一个）<textarea data-axis-values rows="3" placeholder="300 °C&#10;350 °C">${e(values)}</textarea></label><button type="button" class="button small" data-action="template-remove-axis">删除变量</button></div>`;
}
function buildTemplateVariants(meta, form) {
  let variants;
  if (form.get("template_mode") === "rows")
    variants = parseTemplateTable(String(form.get("template_rows") || ""));
  else {
    const axes = [...document.querySelectorAll(".template-axis-row")].map(
      (row) => ({
        name: row.querySelector("[data-axis-name]").value.trim(),
        values: row
          .querySelector("[data-axis-values]")
          .value.split(/\r?\n/)
          .map((value) => value.trim())
          .filter(Boolean),
      }),
    );
    if (
      !axes.length ||
      axes.some(
        (axis) => !axis.name || axis.name.length > 128 || !axis.values.length,
      )
    )
      throw new Error("每个变量都需要参数名和至少一个取值。");
    if (new Set(axes.map((axis) => axis.name)).size !== axes.length)
      throw new Error("变化参数名不能重复。");
    const count = axes.reduce((total, axis) => total * axis.values.length, 1);
    if (count > 100)
      throw new Error(
        `这些取值会组合成 ${count} 个实验，每次最多 100 个，请减少取值或分批。`,
      );
    variants = [{ params: Object.create(null) }];
    for (const axis of axes)
      variants = variants.flatMap((variant) =>
        axis.values.map((value) => ({
          params: { ...variant.params, [axis.name]: value },
        })),
      );
  }
  const common = {
    date: form.get("date"),
    status: form.get("status"),
    batch: form.get("batch"),
    groups: tags(form.get("groups")),
    operator: form.get("operator"),
  };
  return variants.map((variant, index) => {
    const result = { ...common, ...variant };
    result.title =
      result.title ||
      templateTitle(String(form.get("template_pattern")), meta, result, index);
    if (!result.title || Array.from(result.title).length > 256)
      throw new Error(`第 ${index + 1} 行的标题需要 1–256 字。`);
    const dateValue = Date.parse(result.date + "T00:00:00Z");
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(result.date) ||
      !Number.isFinite(dateValue) ||
      new Date(dateValue).toISOString().slice(0, 10) !== result.date
    )
      throw new Error(`第 ${index + 1} 行日期无效，请填写 YYYY-MM-DD。`);
    if (!["planned", "running"].includes(result.status))
      throw new Error("新实验状态请选择计划中或进行中。");
    if (
      Object.entries(result.params || {}).some(
        ([key, value]) => key.length > 128 || String(value).length > 2000,
      )
    )
      throw new Error("参数名最多 128 字，参数值最多 2000 字。");
    return result;
  });
}
function showTemplateResults(result) {
  openDialog(
    "实验已批量生成",
    `<p class="prose">已生成 ${result.count} 个独立实验，保存在当前工作空间。源实验保留，可逐项打开开始记录。</p><div class="template-results">${result.items.map((item) => `<article><div><strong>${scientificHTML(item.title)}</strong><small>${e(item.id)} · ${e(item.device || "尚未填写样品")} · ${statuses[item.status]}</small></div>${button("open-template-result", "打开实验", "arrow", `data-id="${e(item.id)}"`)}</article>`).join("")}</div>`,
    null,
    { wide: true, kicker: "模板：" + result.source_id },
  );
}
async function openBatchExperiments() {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const first = Object.entries(meta.params || {})[0] || ["温度", ""];
  openDialog(
    "按模板批量生成",
    `<p class="prose">模板：${scientificHTML(meta.title)} · ${e(meta.id)}</p><p class="field-hint">继承基本信息、目的、条件与参数；新实验不带结果、测量值、文件、步骤和日志。每次最多 100 个，先预览再生成。</p><div class="form-grid"><div class="form-field wide"><label for="template-mode">生成方式</label><select id="template-mode" name="template_mode"><option value="combinations">组合参数取值</option><option value="rows">每行一个实验（粘贴表格）</option></select></div>${field("template_pattern", "标题格式", "{模板标题} · {序号}", { wide: true, required: true, hint: "支持 {模板标题}、{序号}、{样品}、{分组} 和参数名，如 {退火温度}。表格中有标题的行优先使用该标题。" })}${field("date", "实验日期", templateDate(), { type: "date", required: true })}<div class="form-field"><label for="template-status">新实验状态</label><select id="template-status" name="status"><option value="planned">计划中</option><option value="running">进行中</option></select></div>${field("batch", "本次工艺批次", meta.batch, { hint: "相似的一系列实验可用同一批次，方便检索。" })}${field("operator", "操作人", state.session.operator || meta.operator)}${experimentGroupsField(meta.groups)}${templateOptions()}</div><div id="template-combinations"><div class="form-divider">逐个填写需要改变的参数</div><div id="template-axes">${templateAxisRow(first[0], String(first[1] ?? ""))}</div><button type="button" class="button small" data-action="template-add-axis">${icon("plus")}添加变化参数</button><p class="field-hint">多个变量会生成全部组合，例如 3 个温度 × 2 个时长 = 6 个实验。保持不变的参数直接沿用模板。</p></div><div id="template-rows-section" hidden>${field("template_rows", "条件表格", "标题\t样品\t" + first[0] + "\n", { type: "textarea", wide: true, rows: 7, hint: "可从 Excel 复制或填写 CSV。首行是列名：标题、样品、条件、批次、操作人、日期、状态、分组；其他列作为参数。分组内多个名称用逗号分隔（CSV 单元格须用双引号包住）。空单元格继承模板或上方公共设置。" })}</div><div class="template-preview-actions"><button type="button" class="button" data-action="template-preview">${icon("search")}预览生成结果</button></div><div id="template-preview" class="template-preview" hidden></div>`,
    async (form) => {
      const dialog = state.dialog;
      if (!dialog.templateVariants)
        throw new Error("请先预览当前条件，再确认生成。");
      const result = await runAction("experiments.batch", {
        exp_id: meta.id,
        expected_revision: meta.revision,
        variants: dialog.templateVariants,
        copy_instruments: form.has("copy_instruments"),
        copy_notes: form.has("copy_notes"),
      });
      if (state.dialog !== dialog) {
        toast(`已生成 ${result.count} 个实验，可从任务面板查看结果`);
        return;
      }
      closeDialog(true);
      await renderPage();
      if (!state.dialog) showTemplateResults(result);
    },
    { wide: true, label: "先预览生成结果", kicker: "当前工作空间" },
  );
  const dialog = state.dialog;
  dialog.freezeOnSubmit = true;
  dialog.templateMeta = meta;
  const invalidate = (event) => {
    if (event?.target?.matches("[data-theme-select]")) return;
    dialog.templateVariants = null;
    $("dialog-submit").disabled = true;
    $("dialog-submit").textContent = "先预览生成结果";
    $("template-preview").hidden = true;
  };
  const change = (event) => {
    if (event.target.matches("[data-theme-select]")) return;
    $("template-combinations").hidden =
      $("template-mode").value !== "combinations";
    $("template-rows-section").hidden = $("template-mode").value !== "rows";
    invalidate();
  };
  $("dialog-form").addEventListener("input", invalidate);
  $("dialog-form").addEventListener("change", change);
  dialog.cleanup = () => {
    $("dialog-form").removeEventListener("input", invalidate);
    $("dialog-form").removeEventListener("change", change);
  };
  invalidate();
}
function previewTemplateExperiments() {
  const dialog = state.dialog;
  if (!dialog?.templateMeta) return;
  const variants = buildTemplateVariants(
    dialog.templateMeta,
    new FormData($("dialog-form")),
  );
  const keys = [
    ...new Set(variants.flatMap((value) => Object.keys(value.params || {}))),
  ];
  $("template-preview").innerHTML =
    `<h3>将新建 ${variants.length} 个实验</h3><p class="field-hint">核对标题和变化条件；上方任一设置更改后，需要重新预览。</p><div class="table-wrap"><table><thead><tr><th>序号</th><th>实验标题</th><th>样品</th>${keys.map((key) => `<th>${scientificHTML(key)}</th>`).join("")}<th>测试条件</th><th>实验分组</th></tr></thead><tbody>${variants.map((value, index) => `<tr><td>${index + 1}</td><td>${scientificHTML(value.title)}</td><td>${scientificHTML(value.device ?? dialog.templateMeta.device)}</td>${keys.map((key) => `<td>${scientificHTML(value.params?.[key] ?? dialog.templateMeta.params?.[key] ?? "")}</td>`).join("")}<td>${scientificHTML(value.conditions ?? dialog.templateMeta.conditions)}</td><td>${scientificHTML((value.groups || []).join("、") || "未分组")}</td></tr>`).join("")}</tbody></table></div>`;
  $("template-preview").hidden = false;
  $("template-preview").scrollIntoView({ block: "nearest" });
  dialog.templateVariants = variants;
  $("dialog-submit").disabled = false;
  $("dialog-submit").textContent = `确认生成 ${variants.length} 个实验`;
  $("dialog-error").hidden = true;
}
async function openExperimentEditor(edit = false) {
  const meta = edit
    ? await api("experiment?" + query({ exp_id: state.expId }))
    : {};
  const today = new Date();
  const localDate = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");
  const html = `<div class="form-grid">${field("title", "实验标题", meta.title, { wide: true, required: true })}${field("date", "实验日期", meta.date || localDate, { type: "date", required: true })}<div class="form-field"><label for="field-status">实验状态</label><select id="field-status" name="status" ${meta.archived ? "disabled" : ""}>${Object.entries(
    statuses,
  )
    .filter(([key]) => key !== "archived" || meta.archived)
    .map(
      ([key, value]) =>
        `<option value="${key}" ${meta.status === key ? "selected" : ""}>${value}</option>`,
    )
    .join(
      "",
    )}</select></div>${field("project", "所属课题", edit ? meta.project || "" : state.session.project)}${field("operator", "操作人", edit ? meta.operator || "" : state.session.operator)}${field("device", "器件 / 样品编号", meta.device)}${field("batch", "工艺批次", meta.batch)}${field("category", "实验类别", meta.category)}${field("tags", "实验标签", (meta.tags || []).join(", "), { hint: "逗号分隔，与文件自己的标签相互独立。" })}${experimentGroupsField(meta.groups)}${field("purpose", "实验目的", meta.purpose, { type: "textarea", wide: true })}${field("conditions", "测试条件", meta.conditions, { type: "textarea", wide: true })}${edit ? field("results", "结果与观察", meta.results, { type: "textarea", wide: true }) + field("conclusion", "结论与下一步", meta.conclusion, { type: "textarea", wide: true }) : ""}${field("notes", "实验备注", meta.notes, { type: "textarea", wide: true })}</div><div class="form-divider">工艺 / 测试参数</div><div id="parameter-rows">${
    Object.entries(meta.params || {})
      .map(([key, value]) => parameterRow(key, value))
      .join("") || parameterRow()
  }</div><button type="button" class="button small" data-action="add-parameter">${icon("plus")}添加参数</button>`;
  openDialog(
    edit ? "编辑实验" : "新建一项实验",
    html,
    async (form) => {
      const values = Object.fromEntries(form);
      values.tags = tags(values.tags);
      values.groups = tags(values.groups);
      values.params = collectParameters(meta.params || {});
      if (values.status === "archived") delete values.status;
      const result = await runAction(
        edit ? "experiments.update" : "experiments.create",
        edit
          ? {
              exp_id: meta.id,
              expected_revision: meta.revision,
              changes: values,
            }
          : values,
      );
      closeDialog(true);
      toast(edit ? "实验记录已保存" : "实验已创建");
      navigate("experiment", result.id);
    },
    {
      wide: true,
      label: edit ? "保存实验" : "创建实验",
      kicker: edit ? meta.id : "NEW EXPERIMENT",
    },
  );
}
function referencesHTML(items = []) {
  if (!items.length) return "";
  return `<div class="record-references" aria-label="引用资料">${items.map((item) => `<div class="record-reference ${item.missing ? "missing" : ""}"><span class="type-icon">${kindIcon(item.path)}</span><div class="reference-description"><strong>${e(item.name || item.path)}</strong><small>来源：${scientificHTML(item.experiment_title || item.exp_id)} · ${e(item.exp_id)}</small><small>${e(item.path)}</small><div class="tag-line">${tagHTML(item.current_tags || item.tags)}</div>${item.missing ? '<span class="reference-warning">原文件缺失或不可访问，请核对来源实验。</span>' : item.changed ? '<span class="reference-warning">原文件已更新，请核对当前版本与引用时记录的摘要。</span>' : item.checksum_unknown ? '<span class="reference-warning">此终端尚无文件校验基线，可在编辑主机扫描来源实验后再核对。</span>' : ""}</div><div class="reference-actions">${item.missing ? "" : `<button class="text-button" type="button" data-action="preview-reference" data-exp="${e(item.exp_id)}" data-path="${e(item.path)}">查看</button><a href="${e(fileURL(item.path, true, item.exp_id))}" download>下载</a>`}</div></div>`).join("")}</div>`;
}
function journalHTML(meta) {
  if (!meta.journal_blocks?.length)
    return '<p class="prose empty">还没有日志，可随时追加观察、异常和后续计划。</p>';
  return meta.journal_blocks
    .map((entry) =>
      entry.kind === "entry"
        ? `<article class="journal-entry"><header><small>${e(entry.author || "未署名")} · ${e(date(entry.created_at, true))}</small><button type="button" class="text-button" data-action="note-references" data-id="${e(entry.id)}">修改引用</button></header><p class="prose">${scientificHTML(entry.text)}</p>${referencesHTML(entry.references)}</article>`
        : entry.text.trim() === `# ${meta.id} 实验日志`
          ? ""
          : `<div class="legacy-journal"><span class="field-hint">以前的日志 / 外部记录</span><p class="prose">${scientificHTML(entry.text)}</p></div>`,
    )
    .join("");
}
function referencePayload() {
  return [...(state.dialog?.referencePicker?.selected.values() || [])].map(
    (item) => ({ exp_id: item.exp_id, path: item.path }),
  );
}
function mountReferencePicker(owner, initial = []) {
  const context = {
    active: true,
    owner,
    selected: new Map(
      initial.map((item) => [JSON.stringify([item.exp_id, item.path]), item]),
    ),
    source: owner.id,
    sourceTitle: owner.title,
    sources: new Map([[owner.id, owner.title]]),
    offset: 0,
    generation: 0,
    expGeneration: 0,
  };
  state.dialog.referencePicker = context;
  const priorCleanup = state.dialog.cleanup;
  state.dialog.cleanup = () => {
    context.active = false;
    clearTimeout(context.fileTimer);
    clearTimeout(context.expTimer);
    priorCleanup?.();
  };
  $("reference-picker").innerHTML =
    `<div class="reference-picker-header"><strong>引用库中已有文件</strong><span class="field-hint">支持图片、视频和数据；可切换来源实验，按文件名或标签查找。</span></div><div class="reference-filters"><input id="reference-exp-query" data-picker-filter type="text" aria-label="搜索来源实验" placeholder="查找实验名称、样品或编号"><select id="reference-source" data-picker-filter aria-label="引用来源实验"><option value="${e(owner.id)}">${e(owner.title)} · 当前实验</option></select><input id="reference-file-query" data-picker-filter type="text" aria-label="搜索引用文件" placeholder="按文件名或标签查找"></div><div id="reference-selected" class="reference-selected"></div><div id="reference-catalog" class="reference-catalog" aria-live="polite">正在读取资料库…</div><div class="reference-pagination"><span id="reference-total"></span><button type="button" id="reference-previous" class="button small">上一页</button><button type="button" id="reference-next" class="button small">下一页</button></div>`;
  const changed = () => {
    if (state.dialog) state.dialog.dirty = true;
    renderSelected();
  };
  function renderSelected() {
    if (!context.active) return;
    const items = [...context.selected.values()];
    $("reference-selected").innerHTML =
      `<strong>已引用 ${items.length} / 100 个文件</strong>${items.length ? items.map((item, index) => `<div class="selected-reference"><div><span>${e(item.name || item.path)}</span><small>${scientificHTML(item.experiment_title || item.exp_id)} · ${e(item.exp_id)}${item.missing ? " · 文件缺失" : ""}</small></div><button class="text-button" type="button" data-remove-reference="${index}" aria-label="移除引用 ${e(item.name || item.path)}">移除</button></div>`).join("") : '<p class="field-hint">勾选下方文件添加引用；不会复制或移动原文件。</p>'}`;
    $("reference-selected")
      .querySelectorAll("[data-remove-reference]")
      .forEach((button) =>
        button.addEventListener("click", () => {
          const item = items[Number(button.dataset.removeReference)];
          context.selected.delete(JSON.stringify([item.exp_id, item.path]));
          changed();
          $("reference-catalog")
            .querySelectorAll("[data-reference-key]")
            .forEach(
              (input) =>
                (input.checked = context.selected.has(
                  input.dataset.referenceKey,
                )),
            );
        }),
      );
  }
  async function loadFiles() {
    if (!context.active) return;
    const generation = ++context.generation,
      source = context.source,
      sourceTitle = context.sourceTitle;
    const keyword = $("reference-file-query").value;
    $("reference-catalog").textContent = "正在读取资料库…";
    try {
      const data = await api(
        "files?" +
          query({
            exp_id: source,
            q: keyword,
            offset: context.offset,
            limit: 50,
          }),
      );
      if (!context.active || generation !== context.generation) return;
      $("reference-total").textContent =
        `共 ${data.total} 项 · 第 ${Math.floor(context.offset / 50) + 1} 页`;
      $("reference-previous").disabled = !context.offset;
      $("reference-next").disabled = !data.has_more;
      $("reference-catalog").innerHTML =
        data.items
          .map((file, index) => {
            const identity = JSON.stringify([source, file.relpath]);
            return `<div class="reference-library-row"><label><input type="checkbox" data-reference-index="${index}" data-reference-key="${e(identity)}" ${context.selected.has(identity) ? "checked" : ""} ${file.missing ? "disabled" : ""}><span><strong>${e(file.relpath.split("/").pop())}</strong><small>${e(file.relpath)} · ${size(file.size)}${file.missing ? " · 文件缺失" : ""}</small><span class="tag-line">${tagHTML(file.tags)}</span></span></label>${file.missing ? "" : `<a class="text-button" target="_blank" rel="noopener noreferrer" href="${e(fileURL(file.relpath, false, source))}">打开原文件 ↗</a>`}</div>`;
          })
          .join("") ||
        '<p class="field-hint">没有匹配的资料，请调整实验或筛选条件；新资料可先导入文件库。</p>';
      $("reference-catalog")
        .querySelectorAll("[data-reference-index]")
        .forEach((input) =>
          input.addEventListener("change", () => {
            const file = data.items[Number(input.dataset.referenceIndex)];
            if (
              input.checked &&
              !context.selected.has(input.dataset.referenceKey) &&
              context.selected.size >= 100
            ) {
              input.checked = false;
              toast("每条记录最多引用 100 个文件", true);
              return;
            }
            if (input.checked)
              context.selected.set(input.dataset.referenceKey, {
                exp_id: source,
                path: file.relpath,
                name: file.relpath.split("/").pop(),
                experiment_title: sourceTitle,
                tags: file.tags,
              });
            else context.selected.delete(input.dataset.referenceKey);
            changed();
          }),
        );
    } catch (error) {
      if (context.active && generation === context.generation) {
        $("reference-catalog").innerHTML =
          `<p class="reference-warning">${e(errorText(error))}</p><button class="button small" type="button" id="reference-retry">重新读取</button>`;
        $("reference-retry").addEventListener("click", loadFiles);
      }
    }
  }
  async function loadExperiments() {
    const generation = ++context.expGeneration;
    try {
      const data = await api(
        "experiments?" +
          query({ q: $("reference-exp-query").value, limit: 50, archived: 1 }),
      );
      if (!context.active || generation !== context.expGeneration) return;
      if (!data.items.some((item) => item.id === context.source))
        data.items.unshift({ id: context.source, title: context.sourceTitle });
      data.items.forEach((item) => context.sources.set(item.id, item.title));
      $("reference-source").innerHTML = data.items
        .map(
          (item) =>
            `<option value="${e(item.id)}" ${item.id === context.source ? "selected" : ""}>${e(item.title)} · ${e(item.id)}${item.id === owner.id ? " · 当前实验" : ""}</option>`,
        )
        .join("");
    } catch (error) {
      if (context.active) toast(errorText(error), true);
    }
  }
  $("reference-source").addEventListener("change", (event) => {
    context.source = event.target.value;
    context.sourceTitle = context.sources.get(context.source) || context.source;
    context.offset = 0;
    loadFiles();
  });
  $("reference-file-query").addEventListener("input", () => {
    clearTimeout(context.fileTimer);
    context.fileTimer = setTimeout(() => {
      context.offset = 0;
      loadFiles();
    }, 250);
  });
  $("reference-exp-query").addEventListener("input", () => {
    clearTimeout(context.expTimer);
    context.expTimer = setTimeout(loadExperiments, 250);
  });
  $("reference-previous").addEventListener("click", () => {
    context.offset = Math.max(0, context.offset - 50);
    loadFiles();
  });
  $("reference-next").addEventListener("click", () => {
    context.offset += 50;
    loadFiles();
  });
  renderSelected();
  Promise.allSettled([loadExperiments(), loadFiles()]);
}
async function openNoteReferences(noteId) {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const entry = meta.journal_blocks.find(
    (item) => item.kind === "entry" && item.id === noteId,
  );
  if (!entry) throw new Error("日志已变化，请刷新后核对原始记录");
  openDialog(
    "修改日志引用",
    `<p class="prose">${scientificHTML(entry.text)}</p><div id="reference-picker"></div>`,
    async () => {
      await runAction("notes.references", {
        exp_id: meta.id,
        note_id: noteId,
        references: referencePayload(),
        expected_revision: meta.revision,
      });
      closeDialog(true);
      toast("日志引用已保存");
      await renderPage();
    },
    { wide: true, label: "保存引用", kicker: meta.title },
  );
  mountReferencePicker(meta, entry.references);
}

async function openStepEditor(number) {
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const step = number
    ? (meta.steps || []).find((item) => item.number === number)
    : null;
  openDialog(
    step ? "修改实验步骤" : "记录实验步骤",
    `<div class="form-grid">${field("title", "步骤标题", step?.title, { wide: true })}${field("text", "操作、条件与观察", step?.text, { type: "textarea", wide: true, rows: 6 })}<div class="form-field wide"><label for="step-files">添加过程图片</label><input id="step-files" type="file" multiple accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/tiff"><p class="field-hint">可添加 PNG / JPEG / GIF / WebP / BMP / TIFF。HEIC 原片可从文件资料导入；浏览器显示能力取决于设备。</p></div>${field("attachment_tags", "新增图片的文件标签", "", { wide: true, hint: "逗号分隔，标签会保存到步骤图片副本。" })}</div>${step?.images?.length ? `<div class="form-divider">已有 ${step.images.length} 张图片</div><label class="checkbox-label"><input type="checkbox" name="clear_images">解除已有图片关联（原有文件保留）</label>` : ""}<div id="reference-picker"></div>`,
    async (form) => {
      const title = String(form.get("title") || ""),
        text = String(form.get("text") || "");
      const selected = referencePayload();
      const attachments = Array.from($("step-files").files);
      if (
        !text.trim() &&
        !selected.length &&
        !attachments.length &&
        (!step?.images?.length || form.has("clear_images"))
      )
        throw new Error("请填写步骤正文、添加图片或引用库中资料");
      const uploadIds = [];
      for (const f of attachments) {
        const entry = {
          file: f,
          relative: f.name,
          tagsText: String(form.get("attachment_tags") || ""),
          state: "",
          key: uuid(),
        };
        const uploaded = await uploadFile(entry, meta.id, false);
        uploadIds.push(uploaded.id);
      }
      const args = {
        exp_id: meta.id,
        title,
        text,
        references: selected,
        upload_ids: uploadIds,
        expected_revision: meta.revision,
      };
      if (step) {
        args.number = step.number;
        args.clear_images = form.has("clear_images");
      }
      await runAction(step ? "steps.update" : "steps.add", args);
      closeDialog(true);
      toast("步骤、图片与引用已保存");
      navigate("experiment", meta.id, "steps");
    },
    { wide: true, label: step ? "保存步骤" : "记录步骤", kicker: meta.title },
  );
  mountReferencePicker(meta, step?.references || []);
}
async function openNote() {
  const expId = state.expId;
  const meta = await api("experiment?" + query({ exp_id: expId }));
  openDialog(
    "追加一条实验日志",
    `<div class="form-grid">${field("text", "观察、异常或后续计划", "", { type: "textarea", wide: true, rows: 7 })}${field("author", "操作人", state.session.operator, { wide: true })}</div><p class="field-hint">保存后追加时间戳，不会覆盖之前的日志。</p><div id="reference-picker"></div>`,
    async (form) => {
      if (!String(form.get("text")).trim()) throw new Error("请先填写日志内容");
      await runAction("notes.add", {
        exp_id: expId,
        text: String(form.get("text")),
        author: String(form.get("author")),
        references: referencePayload(),
        expected_revision: meta.revision,
      });
      closeDialog(true);
      toast("日志已追加");
      navigate("experiment", expId, "steps");
    },
    { label: "保存日志", wide: true },
  );
  mountReferencePicker(meta);
}
async function editFileTags() {
  const paths = [...state.selected];
  if (!paths.length) return;
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const initial =
    paths.length === 1
      ? state.files.find((f) => f.relpath === paths[0])?.tags?.join(", ") || ""
      : "";
  openDialog(
    `修改 ${paths.length} 个文件的标签`,
    `${field("tags", "文件标签", initial, { hint: "支持中文、空格；逗号分隔。选择替换时留空可清除标签。" })}<div class="form-divider">保存方式</div><label class="checkbox-label"><input name="merge" type="checkbox" ${paths.length > 1 ? "checked" : ""}>追加到已有标签，保留原标签</label><ul class="result-list">${paths.map((p) => `<li>${e(p)}</li>`).join("")}</ul>`,
    async (form) => {
      await runAction("files.tags", {
        exp_id: meta.id,
        paths,
        tags: tags(form.get("tags")),
        merge: form.has("merge"),
        expected_revision: meta.revision,
      });
      closeDialog(true);
      toast("文件标签已保存");
      await renderPage();
    },
    { label: "保存文件标签" },
  );
}
async function deleteFiles() {
  const paths = [...state.selected];
  if (!paths.length) return;
  const meta = await api("experiment?" + query({ exp_id: state.expId }));
  const plan = await api(
    "delete-preview?" +
      query({ exp_id: meta.id, paths: JSON.stringify(paths) }),
  );
  openDialog(
    "确认删除所选实验文件",
    `<div class="alert warning">将从本机实验目录删除 ${plan.files} 个文件，共 ${size(plan.bytes)}。如需恢复，必须使用此前的备份。</div><ul class="result-list">${plan.paths.map((p) => `<li>${e(p)}</li>`).join("")}</ul>${field("confirmation", "输入 DELETE 确认删除", "", { required: true })}`,
    async (form) => {
      if (form.get("confirmation") !== "DELETE")
        throw new Error("请输入大写 DELETE 确认");
      await runAction("files.remove", {
        exp_id: meta.id,
        paths: plan.paths,
        confirmation: "DELETE",
        expected_revision: meta.revision,
      });
      closeDialog(true);
      toast("所选文件已删除");
      await renderPage();
    },
    { label: "删除文件" },
  );
  $("dialog-submit").classList.add("danger");
}
async function previewFile(path, expId = state.expId, reference = null) {
  const f =
    reference ||
    (expId === state.expId
      ? state.files.find((item) => item.relpath === path)
      : null);
  const type = kind(path);
  let html;
  if (type === "图片")
    html = `<img id="preview-image" class="preview-media" src="${e(fileURL(path, false, expId))}" alt="${e(path)}"><p id="preview-fallback" class="field-hint" hidden>当前浏览器无法显示此图片格式，可下载原文件打开。</p>`;
  else if (type === "视频")
    html = `<video class="preview-media" src="${e(fileURL(path, false, expId))}" controls preload="metadata"></video><p class="field-hint">若浏览器不支持当前视频编码，可下载原片使用本机应用播放。</p>`;
  else {
    try {
      const value = await api("file-text?" + query({ exp_id: expId, path }));
      html = `<pre class="preview-text">${e(value.text)}</pre>${value.truncated ? '<p class="field-hint">此处仅展示前 200 KB，完整内容请下载。</p>' : ""}`;
    } catch (error) {
      html = `<div class="alert">${e(errorText(error))}</div>`;
    }
  }
  openDialog(
    path,
    `${html}<div class="preview-meta"><span>${e(type)}${f ? " · " + size(f.size) : ""}</span><a class="button small" href="${e(fileURL(path, true, expId))}" download>${icon("download")}下载原文件</a></div><div class="tag-line">${tagHTML(f?.current_tags || f?.tags)}</div>${reference?.changed ? '<p class="alert warning">原文件状态已变化，建议校验来源实验；下面的摘要为引用时记录的版本。</p>' : ""}${f?.sha256 ? `<div class="hash-value">SHA256 · ${e(f.sha256)}</div>` : ""}`,
    null,
    { wide: true, kicker: "文件预览" },
  );
  $("preview-image")?.addEventListener("error", () => {
    $("preview-image").hidden = true;
    $("preview-fallback").hidden = false;
  });
}

async function openImport(showPhone = false) {
  if (!state.expId) {
    navigate("files");
    return;
  }
  const expId = state.expId;
  const uploads = await api("uploads?" + query({ exp_id: expId }));
  state.queue = uploads.items.map((item) => ({
    key: uuid(),
    uploadedId: item.id,
    relative: item.relative,
    name: item.name,
    size: item.size,
    tagsText: (item.tags || []).join(", "),
    checked: true,
    state: "已传到电脑 · 等待导入",
  }));
  openDialog(
    "导入实验资料",
    `<div class="alert">${icon("info")}工作空间：${scientificHTML(state.session.workspace?.name || state.session.project)} · 目标实验：${scientificHTML(state.meta?.title || expId)}。上传先进入暂存，确认导入后才归入实验并登记哈希。</div><div class="form-divider">添加图片、视频或数据</div><div id="upload-drop" class="upload-drop">${icon("upload")}<h3>拖入文件，或从设备中选择</h3><p>图片、视频、CSV / Excel 与仪器原始文件，保留原格式。</p><div class="toolbar-group"><label class="button small" for="upload-files">选择文件</label><label class="button small" for="upload-folder">选择文件夹</label></div><input id="upload-files" type="file" multiple hidden><input id="upload-folder" type="file" multiple webkitdirectory hidden></div><div class="batch-toolbar"><input id="upload-batch-tags" type="text" placeholder="批量标签，例如 样品 A, 退火前" aria-label="批量文件标签"><button class="button small" type="button" data-action="upload-batch-tags">追加到勾选文件</button></div><div id="upload-list" class="upload-list"></div><div class="upload-options"><label for="upload-role">存放位置</label><select id="upload-role"><option value="auto">按类型：图片 / 视频 → 记录；数据 → 原始数据</option>${state.session.roles.map((r) => `<option value="${e(r.role)}">${e(r.label)}</option>`).join("")}</select><label class="checkbox-label"><input id="upload-deduplicate" type="checkbox" checked>相同内容去重并合并标签</label><button type="button" class="button small subtle" data-action="stop-upload">停止当前发送</button></div><p class="field-hint">手机使用系统文件选择器可选照片和视频。校园网隔离时请使用独立热点、USB 共享网络，或先从手机 / U 盘复制到电脑后在本机浏览器选择。</p><button class="button small subtle" type="button" data-action="discard-uploads">移除勾选的暂存文件</button>`,
    async () => {
      const selected = state.queue.filter((item) => item.checked && !item.done);
      if (!selected.length) throw new Error("请先选择或勾选需要导入的文件");
      const failures = [];
      for (const item of selected) {
        if (item.uploadedId) continue;
        try {
          const uploaded = await uploadFile(item, expId);
          item.uploadedId = uploaded.id;
          item.state = "已传到电脑 · 等待导入";
        } catch (error) {
          item.state = "发送失败：" + errorText(error);
          failures.push(item);
        }
        renderUploadList();
      }
      const ready = selected.filter((item) => item.uploadedId);
      if (ready.length) {
        const labels = {};
        ready.forEach((item) => {
          labels[item.uploadedId] = tags(item.tagsText);
        });
        const result = await runAction("files.import", {
          exp_id: expId,
          upload_ids: ready.map((item) => item.uploadedId),
          role: $("upload-role").value,
          deduplicate: $("upload-deduplicate").checked,
          file_tags: labels,
        });
        const completed = new Set([...result.imported, ...result.duplicates]);
        for (const item of ready) {
          if (completed.has(item.uploadedId)) {
            item.done = true;
            item.checked = false;
            item.state = result.duplicates.includes(item.uploadedId)
              ? "重复已跳过 · 标签已合并"
              : "已导入并完成校验";
          } else {
            item.state =
              result.errors.find((error) => error.key === item.uploadedId)
                ?.error || "未完成，可重试";
          }
        }
        renderUploadList();
        if (!failures.length && !result.errors.length && !result.cancelled) {
          closeDialog(true);
          toast(
            `已导入 ${result.imported.length} 个，重复 ${result.duplicates.length} 个`,
          );
          await renderPage();
          return;
        }
      }
      $("dialog-error").textContent =
        "部分文件未完成，成功文件已保留；请检查清单后重试。";
      $("dialog-error").hidden = false;
    },
    { wide: true, label: "发送并导入", kicker: "文件、视频与数据" },
  );
  const transfer = {
    expId,
    active: true,
    busy: false,
    connectivity: null,
    pairing: null,
  };
  state.dialog.transfer = transfer;
  state.dialog.cleanup = () => {
    transfer.active = false;
    clearTimeout(transfer.timer);
  };
  $("upload-drop").insertAdjacentHTML(
    "beforebegin",
    `
    <div class="transfer-methods">
      <details id="phone-panel" class="transfer-method">
        <summary>手机扫码传入 <span>校园网可切换电脑热点</span></summary>
        <p class="field-hint">同一校园网也可能无法互通。建议先连电脑热点，再扫描资料二维码；保持手机屏幕点亮。</p>
        ${state.session.storage?.mode === "writer" ? '<p class="field-hint">手机扫码后可选择直传到 NAS 的当前实验，或先发送到电脑暂存。</p>' : ""}
        <div id="phone-controls"><p class="field-hint">正在读取电脑网卡…</p></div>
        <p id="phone-error" class="error-text" role="status" hidden></p>
        <div id="phone-codes" class="phone-codes"></div>
      </details>
      <div class="transfer-method wechat-transfer">
        <strong>微信网页文件传输助手</strong>
        <p>校园网无法直连时，可通过微信中转。手机扫码打开官方助手，发送后在网页下载原文件，再用下方「选择文件」导入并打标签。</p>
        <p class="original-reminder"><strong>照片请勾选「原图」；视频优先按「文件」发送原始视频，避免压缩。</strong></p>
        <a class="button small" href="https://filehelper.weixin.qq.com/" target="_blank" rel="noopener noreferrer">打开微信文件传输助手 ↗</a>
      </div>
    </div>`,
  );
  $("phone-panel").addEventListener("toggle", () => {
    if ($("phone-panel")?.open && !transfer.connectivity && !transfer.busy)
      phoneAction("phone-refresh");
  });
  if (showPhone) $("phone-panel").open = true;
  pollPhoneInbox(transfer);
  renderUploadList();
  const addFiles = (files) => {
    for (const file of files) {
      const relative = file.webkitRelativePath || file.name;
      const key = [relative, file.size, file.lastModified].join("|");
      if (state.queue.some((item) => item.sourceKey === key && !item.done))
        continue;
      state.queue.push({
        key: uuid(),
        sourceKey: key,
        file,
        relative,
        name: file.name,
        size: file.size,
        tagsText: $("upload-batch-tags").value,
        checked: true,
        state: "待发送",
      });
    }
    if (state.dialog) state.dialog.dirty = true;
    renderUploadList();
  };
  $("upload-files").addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";
  });
  $("upload-folder").addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";
  });
  const drop = $("upload-drop");
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("dragging");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("dragging");
    addFiles(event.dataTransfer.files);
  });
}

function renderPhone(transfer) {
  if (!transfer.active || !transfer.connectivity) return;
  const data = transfer.connectivity,
    hotspot = data.hotspot;
  const previous = $("phone-interface")?.value;
  $("phone-controls").innerHTML = `
    ${data.local_controls ? `<div class="toolbar-group">${button("hotspot-start", "一键开启 Windows 热点并生成二维码", "upload")}${button("hotspot-refresh", "刷新热点状态", "refresh")}${button("hotspot-settings", "打开系统热点设置", "settings")}${hotspot?.started_by_app ? button("hotspot-stop", "关闭本次开启的热点", "close") : ""}</div>` : data.windows ? '<p class="field-hint">请在电脑本机浏览器（127.0.0.1）中开启热点并读取连接二维码。</p>' : '<p class="field-hint">Mac 可使用已有热点、USB 共享网络或可互通的局域网。</p>'}
    ${hotspot ? `<p class="field-hint">${e(hotspot.message)}${hotspot.started_by_app ? "；关闭网页服务时会尝试关闭本次开启的热点。" : ""}</p>` : ""}
    <div class="network-picker"><label for="phone-interface">电脑接收网卡</label><select id="phone-interface" data-transfer-control>${data.interfaces.map((item) => `<option value="${e(item.address)}" ${previous === item.address ? "selected" : ""}>${e(item.name)} · ${e(item.address)}${item.hotspot ? " · 热点" : ""}</option>`).join("")}</select>${button("phone-create", "生成资料二维码", "upload")}${button("phone-refresh", "刷新网卡", "refresh")}</div>
    ${!data.interfaces.length ? '<p class="field-hint">未发现可用的局域网 IPv4 地址。请连接网络 / 开启热点后刷新，也可使用微信或 U 盘传入。</p>' : ""}`;
  const pair = transfer.pairing;
  $("phone-codes").innerHTML = `
    ${hotspot?.wifi_qr ? `<section class="qr-card"><strong>1 · 扫码连接电脑热点</strong><div class="qr-image">${hotspot.wifi_qr}</div><span>${e(hotspot.ssid)}</span><details><summary>查看连接密码</summary><span class="mono">${e(hotspot.password)}</span></details><p>使用手机系统相机或 WLAN 扫一扫连接；不识别时可手动输入名称和密码。</p></section>` : ""}
    ${pair ? `<section class="qr-card"><strong>${hotspot?.wifi_qr ? "2" : "下一步"} · 扫码传入实验资料</strong><div class="qr-image">${pair.qr_svg}</div><p>已接收 ${pair.received} 个文件 · 剩余 ${Math.ceil(pair.expires_in / 60)} 分钟。此链接仅接收当前实验的文件。</p><a class="text-button" href="${e(pair.url)}" target="_blank" rel="noopener noreferrer">打开手机上传页 ↗</a><input aria-label="手机资料上传链接" type="text" value="${e(pair.url)}" readonly><button type="button" class="button small" data-action="phone-stop">关闭此资料二维码</button></section>` : ""}`;
  $("phone-controls")
    .querySelectorAll("button")
    .forEach((button) => (button.disabled = transfer.busy));
  if (!data.interfaces.length)
    $("phone-controls").querySelector('[data-action="phone-create"]').disabled =
      true;
}

async function phoneAction(action) {
  const transfer = state.dialog?.transfer;
  if (!transfer?.active || transfer.busy) return;
  const host = $("phone-interface")?.value;
  transfer.busy = true;
  renderPhone(transfer);
  $("phone-error").hidden = true;
  try {
    if (action === "phone-refresh") {
      const data = await api("connectivity");
      transfer.connectivity = {
        ...data,
        hotspot: transfer.connectivity?.hotspot || null,
      };
    } else if (action.startsWith("hotspot-")) {
      const data = await api("connectivity/hotspot", {
        method: "POST",
        body: JSON.stringify({
          action: action.slice(8) === "refresh" ? "status" : action.slice(8),
        }),
      });
      if (action !== "hotspot-settings") transfer.connectivity = data;
      if (
        action === "hotspot-start" &&
        data.hotspot?.ok &&
        data.hotspot.state === "On"
      ) {
        const address = data.interfaces.find((item) => item.hotspot)?.address;
        if (address)
          transfer.pairing = await api("pairings", {
            method: "POST",
            body: JSON.stringify({ exp_id: transfer.expId, host: address }),
          });
        else
          throw new Error(
            "热点已开启，尚未识别到热点网卡。请稍后刷新热点状态，选择实际热点地址后生成资料二维码。",
          );
      }
      if (action === "hotspot-stop" && data.hotspot?.state === "Off")
        transfer.pairing = null;
      if (
        action === "hotspot-start" &&
        data.hotspot?.state === "On" &&
        !data.hotspot.wifi_qr
      )
        throw new Error(
          "热点已开启，但无法读取名称和密码。请打开系统热点设置查看，连接后再扫描资料二维码。",
        );
      if (data.hotspot && !data.hotspot.ok)
        throw new Error(data.hotspot.message);
    } else if (action === "phone-create") {
      if (!host) throw new Error("请先选择可用的接收网卡");
      transfer.pairing = await api("pairings", {
        method: "POST",
        body: JSON.stringify({ exp_id: transfer.expId, host }),
      });
    } else if (action === "phone-stop" && transfer.pairing) {
      await api("pairings/stop", {
        method: "POST",
        body: JSON.stringify({ id: transfer.pairing.id }),
      });
      transfer.pairing = null;
    }
  } catch (error) {
    if (transfer.active) {
      if (!transfer.connectivity)
        $("phone-controls").innerHTML = button(
          "phone-refresh",
          "重试读取网卡",
          "refresh",
        );
      $("phone-error").textContent = errorText(error);
      $("phone-error").hidden = false;
    }
  } finally {
    transfer.busy = false;
    renderPhone(transfer);
  }
}

async function pollPhoneInbox(transfer) {
  try {
    const uploads = await api("uploads?" + query({ exp_id: transfer.expId }));
    if (!transfer.active) return;
    if ($("dialog-submit").disabled || state.uploadXHR) {
      transfer.timer = setTimeout(() => pollPhoneInbox(transfer), 2000);
      return;
    }
    let added = false;
    for (const item of uploads.items) {
      if (state.queue.some((entry) => entry.uploadedId === item.id)) continue;
      state.queue.push({
        key: uuid(),
        uploadedId: item.id,
        relative: item.relative,
        name: item.name,
        size: item.size,
        tagsText: (item.tags || []).join(", "),
        checked: true,
        state: "已传到电脑 · 等待导入",
      });
      added = true;
    }
    if (added) renderUploadList();
    if (transfer.pairing && !transfer.busy) {
      try {
        const pair = await api(
          "pairings?" + query({ id: transfer.pairing.id }),
        );
        if (transfer.active && transfer.pairing?.id === pair.id) {
          const changed =
            transfer.pairing.received !== pair.received ||
            Math.ceil(transfer.pairing.expires_in / 60) !==
              Math.ceil(pair.expires_in / 60);
          transfer.pairing = pair;
          if (changed) renderPhone(transfer);
        }
      } catch (error) {
        if (transfer.active && error.status === 404) {
          transfer.pairing = null;
          renderPhone(transfer);
        }
      }
    }
  } catch {
    /* A later poll recovers after a transient network failure. */
  }
  if (transfer.active)
    transfer.timer = setTimeout(() => pollPhoneInbox(transfer), 2000);
}
function renderUploadList() {
  if (!$("upload-list")) return;
  $("upload-list").innerHTML =
    state.queue
      .map(
        (item, index) =>
          `<div class="upload-row"><input type="checkbox" data-upload-check="${index}" ${item.checked ? "checked" : ""} ${item.done ? "disabled" : ""} aria-label="选择 ${e(item.relative)}"><div><strong>${e(item.relative)}</strong><small>${size(item.size)} · ${e(item.state)}</small></div><input type="text" data-upload-tags="${index}" value="${e(item.tagsText)}" placeholder="此文件的标签，逗号分隔" aria-label="${e(item.relative)} 的文件标签" ${item.done ? "disabled" : ""}></div>`,
      )
      .join("") ||
    '<div class="task-empty">尚未添加文件。刷新页面后，已上传的暂存文件仍可恢复。</div>';
  document.querySelectorAll("[data-upload-check]").forEach((input) =>
    input.addEventListener("change", () => {
      state.queue[Number(input.dataset.uploadCheck)].checked = input.checked;
    }),
  );
  document.querySelectorAll("[data-upload-tags]").forEach((input) =>
    input.addEventListener("input", () => {
      state.queue[Number(input.dataset.uploadTags)].tagsText = input.value;
    }),
  );
}
function uploadFile(item, expId, updateQueue = true) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.uploadXHR = xhr;
    xhr.open("POST", "/api/uploads?" + query({ exp_id: expId }));
    xhr.timeout = 3600000;
    if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
    xhr.setRequestHeader("X-Expvault-Request", "1");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(item.file.name));
    xhr.setRequestHeader("X-File-Relative", encodeURIComponent(item.relative));
    xhr.setRequestHeader(
      "X-File-Tags",
      encodeURIComponent(item.tagsText || ""),
    );
    const finish = () => {
      if (state.uploadXHR === xhr) state.uploadXHR = null;
    };
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        item.state =
          "发送中 " +
          Math.round((event.loaded / Math.max(event.total, 1)) * 100) +
          "%";
        if (updateQueue) renderUploadList();
      }
    };
    xhr.onload = () => {
      finish();
      let value;
      try {
        value = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error("电脑端返回了无效响应，请刷新暂存列表核对"));
        return;
      }
      xhr.status === 201
        ? resolve(value)
        : reject(new Error(value.error || "上传失败"));
    };
    xhr.onerror = () => {
      finish();
      reject(new Error("连接中断，请检查网络并重新打开暂存清单核对"));
    };
    xhr.onabort = () => {
      finish();
      reject(new Error("发送已停止"));
    };
    xhr.ontimeout = () => {
      finish();
      reject(new Error("发送超时，请保持浏览器前台后重试"));
    };
    xhr.send(item.file);
  });
}

function rememberJob(id) {
  let ids = [];
  try {
    ids = JSON.parse(sessionStorage.getItem("expvault.web.jobs") || "[]");
  } catch {}
  sessionStorage.setItem(
    "expvault.web.jobs",
    JSON.stringify([...new Set([id, ...ids])].slice(0, 30)),
  );
}
function renderTasks() {
  const jobs = [...state.jobs.values()].reverse().slice(0, 15);
  const count = jobs.filter((job) => pendingStatuses.has(job.status)).length;
  $("task-count").textContent = String(count);
  $("task-count").hidden = !count;
  $("task-list").innerHTML =
    jobs
      .map(
        (job) =>
          `<article class="task-row"><strong>${e(operationNames[job.operation] || job.operation)}</strong><p>${e(job.message)}</p>${pendingStatuses.has(job.status) ? `<progress max="100" value="${job.progress || 0}"></progress>` : ""}<footer><span>${e({ queued: "等待中", running: "进行中", succeeded: "已完成", failed: "未完成", partial: "部分完成", cancelled: "已取消", interrupted: "需要核对", unconfirmed: "连接中断" }[job.status] || job.status)}</span>${pendingStatuses.has(job.status) && job.can_cancel ? `<button class="text-button" data-action="cancel-job" data-id="${e(job.id)}">取消导入</button>` : ""}${job.status === "succeeded" && ["experiments.verify", "backup.create", "reports.export", "experiments.duplicate", "experiments.batch"].includes(job.operation) ? `<button class="text-button" data-action="job-result" data-id="${e(job.id)}">查看结果</button>` : ""}</footer></article>`,
      )
      .join("") || '<div class="task-empty">还没有后台任务</div>';
}
function watchJob(job) {
  state.jobs.set(job.id, job);
  renderTasks();
  if (state.watchers.has(job.id)) return state.watchers.get(job.id);
  const promise = (async () => {
    let current = job,
      failures = 0;
    while (pendingStatuses.has(current.status)) {
      await new Promise((resolve) => setTimeout(resolve, 550));
      try {
        current = await api("jobs/" + encodeURIComponent(job.id));
        failures = 0;
        state.jobs.set(current.id, current);
        renderTasks();
      } catch (error) {
        if (++failures >= 3) {
          current = {
            ...current,
            status: "unconfirmed",
            message: "连接中断，操作结果未确认。请恢复连接后刷新页面核对。",
          };
          state.jobs.set(current.id, current);
          renderTasks();
          throw error;
        }
      }
    }
    return current;
  })();
  state.watchers.set(job.id, promise);
  promise.finally(() => state.watchers.delete(job.id)).catch(() => {});
  return promise;
}
async function runAction(operation, args) {
  if (
    nasReadOnly() &&
    !["reports.export", "experiments.verify"].includes(operation)
  )
    throw new Error("NAS 当前为只读连接，请在编辑主机修改");
  const requestId = uuid();
  rememberJob(requestId);
  let job;
  try {
    job = await api("actions", {
      method: "POST",
      body: JSON.stringify({
        request_id: requestId,
        operation,
        arguments: args,
      }),
    });
  } catch (error) {
    if (!error.status) {
      try {
        job = await api("jobs/" + requestId);
      } catch {
        throw new Error(
          "连接中断，结果尚未确认。请刷新页面核对，避免重复操作。",
        );
      }
    } else throw error;
  }
  const result = await watchJob(job);
  if (!["succeeded", "partial", "cancelled"].includes(result.status))
    throw new Error(result.message);
  return result.result;
}
async function restoreJobs() {
  let ids = [];
  try {
    ids = JSON.parse(sessionStorage.getItem("expvault.web.jobs") || "[]");
  } catch {}
  try {
    const active = await api("jobs");
    ids = [...new Set([...active.items.map((job) => job.id), ...ids])];
  } catch {}
  await Promise.all(
    ids.slice(0, 30).map(async (id) => {
      try {
        const job = await api("jobs/" + encodeURIComponent(id));
        state.jobs.set(id, job);
        if (pendingStatuses.has(job.status)) watchJob(job).catch(() => {});
      } catch {}
    }),
  );
  renderTasks();
}
function showJobResult(job) {
  if (
    ["experiments.duplicate", "experiments.batch"].includes(job.operation) &&
    state.dialog?.dirty &&
    !confirm("当前窗口有未保存的内容，确定关闭并查看生成结果？")
  )
    return;
  $("task-panel").hidden = true;
  const result = job.result;
  if (!result) return;
  if (job.operation === "experiments.batch") {
    showTemplateResults(result);
    return;
  }
  if (job.operation === "experiments.duplicate") {
    closeDialog(true);
    navigate("experiment", result.id);
    return;
  }
  if (job.operation === "experiments.verify") {
    const groups = [
      ["内容有变化", result.modified],
      ["文件已丢失", result.missing],
      ["尚未登记", result.unregistered],
      ["实验元数据异常", result.bad_meta],
      ["尚无校验基线：请在编辑主机的文件资料中扫描目录", result.unverified],
    ];
    openDialog(
      "实验完整性检查",
      `<div class="alert ${result.healthy ? "success" : "warning"}">${result.healthy ? "文件校验通过，未发现变化、丢失或未登记文件。" : "发现需要核对的文件，请保留原始数据并检查以下结果。"}</div><div class="verify-grid"><div><strong>${result.checked}</strong><small>已检查文件</small></div><div><strong>${result.ok}</strong><small>内容一致</small></div><div><strong>${size(result.bytes)}</strong><small>已检查数据量</small></div></div>${groups
        .filter(([, items]) => items?.length)
        .map(
          ([title, items]) =>
            `<h3 class="section-title">${title} · ${items.length}</h3><ul class="result-list">${items.map((item) => `<li>${e(typeof item === "string" ? item : item.relpath)}</li>`).join("")}</ul>`,
        )
        .join("")}`,
      null,
      { wide: true, kicker: job.exp_id },
    );
  } else if (job.operation === "reports.export") {
    openDialog(
      "实验台账已生成",
      `<p class="prose">${e(result.name)}</p><div class="heading-actions"><a class="button primary" href="${e(result.url)}" download>${icon("download")}下载文件</a></div>`,
      null,
    );
  } else if (job.operation === "backup.create") {
    const stats = result.stats || {};
    openDialog(
      "备份已完成",
      `<div class="alert success">已保存当前实验库快照，原始数据保持不变。</div><div class="verify-grid"><div><strong>${stats.files || 0}</strong><small>文件数量</small></div><div><strong>${size(stats.bytes_total || stats.bytes || 0)}</strong><small>数据总量</small></div><div><strong>${stats.new_objects || 0}</strong><small>新增备份对象</small></div></div><p class="field-hint">可在备份中心查看快照编号和目标。</p>`,
      null,
    );
  }
}

document.addEventListener("click", async (event) => {
  document.querySelectorAll(".experiment-reuse[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  const navigation = event.target.closest("[data-nav]");
  if (navigation) {
    event.preventDefault();
    navigate(
      navigation.dataset.nav,
      navigation.dataset.nav === "files" ? state.expId : "",
    );
    return;
  }
  const control = event.target.closest("[data-action]");
  if (!control || control.disabled) return;
  event.preventDefault();
  const action = control.dataset.action;
  try {
    if (nasReadOnly() && nasWriteActions.has(action))
      throw new Error("NAS 当前为只读连接，请在编辑主机修改");
    if (action === "nas-libraries") await openNASLibraries();
    else if (action === "nas-connect") openNASConnector();
    else if (action === "nas-open")
      await enterNAS({
        connection_id: control.dataset.id,
        mode: control.dataset.mode,
      });
    else if (action === "nas-refresh") {
      const updated = await api("nas/refresh", { method: "POST", body: "{}" });
      state.session.storage = updated.storage;
      await renderPage();
      toast("已从 NAS 更新资料");
    } else if (action === "reload") await renderPage();
    else if (action === "sync-refresh") await refreshSync();
    else if (action === "sync-download-log") await downloadSyncLog();
    else if (action === "sync-configure") await openSyncSettings();
    else if (action === "sync-resolve")
      await openSyncConflict(control.dataset.id);
    else if (["sync-run", "sync-pause", "sync-resume"].includes(action)) {
      await api("sync/action", {
        method: "POST",
        body: JSON.stringify({ action: action.slice(5) }),
      });
      await refreshSync();
    } else if (action === "sync-pairing") {
      const [invite, pairing] = await Promise.all(["pairing-code", "pairing"].map((action) =>
        api("sync/action", { method: "POST", body: JSON.stringify({ action }) })
      ));
      openDialog(
        "局域网同步配对信息",
        `${invite.code ? `<p class="prose">在另一台电脑的同步设置中选择「连接另一电脑」，从在线主机中选择本机并输入以下配对码。</p><p><strong class="mono" id="sync-invite-code">${e(invite.code)}</strong></p><p class="field-hint">配对码有效期 5 分钟，仅可使用一次。每次重新打开此窗口都会生成新配对码。</p>` : '<p class="field-hint">局域网发现端口不可用。请使用手动配对。</p>'}<details class="sync-help"><summary>手动配对</summary><p class="field-hint">这份信息包含同步密钥，请只交给可信电脑。</p><textarea class="sync-pairing-code" rows="7" readonly aria-label="局域网同步配对信息">${e(JSON.stringify(pairing, null, 2))}</textarea></details>`,
        null,
        { wide: true },
      );
    } else if (action === "workspaces") await openWorkspaces();
    else if (action === "new-workspace") openWorkspaceCreator();
    else if (action === "select-workspace")
      await enterWorkspace(control.dataset.id);
    else if (action === "overview-filter" && control.dataset.filter === "files")
      navigate("files");
    else if (action === "show-running" || action === "overview-filter") {
      const filter = action === "show-running" ? "running" : control.dataset.filter;
      state.status = filter === "all" ? "" : filter;
      state.listQuery = "";
      state.groupFilter = "";
      state.archived = filter === "all";
      state.page = 0;
      clearExperimentSelection();
      navigate("experiments");
    } else if (action === "active-previous" || action === "active-next") {
      state.activePage += action === "active-next" ? 1 : -1;
      await renderPage();
      document
        .querySelector(".active-section")
        ?.scrollIntoView({ block: "start" });
    } else if (action === "edit-status") {
      await openStatusEditor(control.dataset.id);
    } else if (action === "pin-parameter") {
      const key = control.dataset.param;
      const pins = pinnedParameters(state.expId).filter((name) =>
        Object.hasOwn(state.meta.params || {}, name),
      );
      if (!Object.hasOwn(state.meta.params || {}, key)) return;
      if (!pins.includes(key) && pins.length >= 6)
        throw new Error("最多置顶 6 项参数，请先取消一项置顶");
      const updated = pins.includes(key)
        ? pins.filter((item) => item !== key)
        : [...pins, key];
      parameterPins.set(parameterPreferenceKey(state.expId), updated);
      try {
        localStorage.setItem(
          parameterPreferenceKey(state.expId),
          JSON.stringify(updated),
        );
      } catch {
        toast("已置顶；浏览器未允许存储，偏好仅在本次页面中有效。", true);
      }
      $("experiment-parameters").innerHTML = parameterPanel(state.meta);
      applyNASReadOnly();
      [...$("experiment-parameters").querySelectorAll("[data-param]")]
        .find((button) => button.dataset.param === key)
        ?.focus();
    } else if (action === "toggle-detail-panel") {
      const panel = control.dataset.panel;
      if (!["parameters", "measurements"].includes(panel) || !state.meta)
        return;
      const key = detailPanelPreferenceKey(state.expId, panel);
      const collapsed = !detailPanelCollapsed(state.expId, panel);
      collapsedDetailPanels.set(key, collapsed);
      try {
        localStorage.setItem(key, String(collapsed));
      } catch {
        toast(
          "显示状态已切换；浏览器未允许存储，偏好仅在本次页面中有效。",
          true,
        );
      }
      const container = $("experiment-" + panel);
      container.innerHTML =
        panel === "parameters"
          ? parameterPanel(state.meta)
          : measurementPanel(state.meta);
      applyNASReadOnly();
      container
        .querySelector('[data-action="toggle-detail-panel"]')
        ?.focus({ preventScroll: true });
    } else if (action === "toggle-parameters") {
      state.parametersExpanded = !state.parametersExpanded;
      $("experiment-parameters").innerHTML = parameterPanel(state.meta);
      applyNASReadOnly();
      $("experiment-parameters")
        .querySelector('[data-action="toggle-parameters"]')
        ?.focus({ preventScroll: true });
    } else if (action === "close-dialog") closeDialog();
    else if (action === "add-instrument") await openInstrumentEditor();
    else if (action === "edit-instrument")
      await openInstrumentEditor(control.dataset.group);
    else if (action === "add-measurement")
      await openMeasurementEditor(control.dataset.group);
    else if (action === "edit-measurement" || action === "copy-measurement")
      await openMeasurementEditor(
        control.dataset.group,
        control.dataset.run,
        action === "copy-measurement",
      );
    else if (action === "new-experiment") await openExperimentEditor();
    else if (action === "edit-experiment") await openExperimentEditor(true);
    else if (action === "manage-experiment-groups") await openGroupManager();
    else if (action === "edit-experiment-groups") await openExperimentGroups();
    else if (action === "bulk-add-groups") await openBulkGroups("add");
    else if (action === "bulk-remove-groups") await openBulkGroups("remove");
    else if (action === "clear-experiment-selection")
      clearExperimentSelection();
    else if (action === "filter-experiment-group") {
      closeDialog();
      if (state.dialog) return;
      clearExperimentSelection();
      state.groupFilter = "group:" + control.dataset.groupName;
      state.listQuery = "";
      state.status = "";
      state.page = 0;
      navigate("experiments");
    } else if (action === "rename-experiment-group")
      await renameExperimentGroup(control.dataset.groupName);
    else if (action === "dissolve-experiment-group") {
      const name = control.dataset.groupName;
      if (
        !confirm(`解散分组「${name}」？只移除分组关系，组内实验和资料会保留。`)
      )
        return;
      const dialog = state.dialog;
      await runAction("groups.dissolve", { name });
      if (state.groupFilter === "group:" + name) state.groupFilter = "";
      clearExperimentSelection();
      if (state.dialog === dialog) {
        closeDialog(true);
        await renderPage();
        await openGroupManager();
      }
      toast("分组已解散，实验和资料已保留");
    } else if (action === "duplicate-experiment")
      await openDuplicateExperiment();
    else if (action === "batch-experiments") await openBatchExperiments();
    else if (action === "template-preview") previewTemplateExperiments();
    else if (action === "template-add-axis") {
      if (document.querySelectorAll(".template-axis-row").length >= 4)
        throw new Error("最多同时组合 4 个变化参数。");
      $("template-axes").insertAdjacentHTML("beforeend", templateAxisRow());
      $("dialog-form").dispatchEvent(new Event("input", { bubbles: true }));
    } else if (action === "template-remove-axis") {
      control.closest(".template-axis-row").remove();
      $("dialog-form").dispatchEvent(new Event("input", { bubbles: true }));
    } else if (action === "open-template-result") {
      closeDialog(true);
      navigate("experiment", control.dataset.id);
    } else if (action === "edit-parameters") {
      await openExperimentEditor(true);
      $("parameter-rows").scrollIntoView({ block: "center" });
      $("parameter-rows")
        .querySelector("input")
        ?.focus({ preventScroll: true });
    } else if (action === "open-experiment")
      navigate("experiment", control.dataset.id);
    else if (action === "detail-tab") {
      state.tab = control.dataset.tab;
      history.replaceState(
        null,
        "",
        "/?" +
          query({ view: "experiment", exp_id: state.expId, tab: state.tab }),
      );
      await renderPage();
      $("detail-tab-" + state.tab)?.focus({ preventScroll: true });
    } else if (action === "previous-page") {
      state.page--;
      await renderPage();
    } else if (action === "next-page") {
      state.page++;
      await renderPage();
    } else if (action === "files-previous") {
      state.filePage--;
      await renderPage();
    } else if (action === "files-next") {
      state.filePage++;
      await renderPage();
    } else if (action === "add-parameter") {
      $("parameter-rows").insertAdjacentHTML("beforeend", parameterRow());
      setupScientificInputs();
    } else if (action === "remove-parameter")
      control.closest(".parameter-row").remove();
    else if (action === "add-step") await openStepEditor();
    else if (action === "edit-step")
      await openStepEditor(Number(control.dataset.number));
    else if (action === "add-note") await openNote();
    else if (action === "note-references")
      await openNoteReferences(control.dataset.id);
    else if (action === "preview-reference") {
      const all = [
        ...(state.meta?.steps || []),
        ...(state.meta?.journal_blocks || []),
      ].flatMap((record) => record.references || []);
      const reference = all.find(
        (item) =>
          item.exp_id === control.dataset.exp &&
          item.path === control.dataset.path,
      );
      await previewFile(control.dataset.path, control.dataset.exp, reference);
    } else if (action === "edit-file-tags") await editFileTags();
    else if (action === "delete-files") await deleteFiles();
    else if (action === "preview-file") await previewFile(control.dataset.path);
    else if (action === "import") await openImport();
    else if (action === "phone-import") await openImport(true);
    else if (action.startsWith("phone-") || action.startsWith("hotspot-"))
      await phoneAction(action);
    else if (action === "upload-batch-tags") {
      const values = tags($("upload-batch-tags").value);
      state.queue
        .filter((item) => item.checked && !item.done)
        .forEach((item) => {
          item.tagsText = [
            ...new Set([...tags(item.tagsText), ...values]),
          ].join(", ");
        });
      renderUploadList();
    } else if (action === "stop-upload") state.uploadXHR?.abort();
    else if (action === "discard-uploads") {
      const selected = state.queue.filter((item) => item.checked && !item.done);
      if (!selected.length) return;
      if (
        !confirm(`移除 ${selected.length} 项暂存？已导入的实验文件不会受影响。`)
      )
        return;
      const ids = selected
        .filter((item) => item.uploadedId)
        .map((item) => item.uploadedId);
      if (ids.length)
        await runAction("uploads.discard", {
          exp_id: state.expId,
          upload_ids: ids,
        });
      const keys = new Set(selected.map((item) => item.key));
      state.queue = state.queue.filter((item) => !keys.has(item.key));
      renderUploadList();
    } else if (action === "archive") {
      const meta = await api("experiment?" + query({ exp_id: state.expId }));
      if (
        !confirm(
          meta.archived
            ? "恢复此实验到已完成状态？"
            : "归档此实验？全部数据和记录都会保留。",
        )
      )
        return;
      await runAction("experiments.archive", {
        exp_id: meta.id,
        archived: !meta.archived,
        expected_revision: meta.revision,
      });
      toast(meta.archived ? "实验已恢复" : "实验已归档");
      await renderPage();
    } else if (action === "scan") {
      await runAction("experiments.scan", { exp_id: state.expId });
      toast("目录扫描完成，文件已登记");
      await renderPage();
    } else if (action === "verify" || action === "verify-selected") {
      const expId =
        action === "verify-selected"
          ? $("verify-experiment").value
          : state.expId;
      $("task-panel").hidden = false;
      await runAction("experiments.verify", { exp_id: expId });
      const job = [...state.jobs.values()]
        .reverse()
        .find(
          (item) =>
            item.operation === "experiments.verify" && item.exp_id === expId,
        );
      showJobResult(job);
    } else if (action === "backup") {
      $("task-panel").hidden = false;
      await runAction("backup.create", { target: control.dataset.target });
      $("task-panel").hidden = true;
      toast("备份已完成");
      await renderPage();
    } else if (action === "export-html" || action === "export-xlsx") {
      await runAction("reports.export", {
        format: action === "export-html" ? "html" : "xlsx",
      });
      const job = [...state.jobs.values()]
        .reverse()
        .find((item) => item.operation === "reports.export");
      showJobResult(job);
    } else if (action === "cancel-job") {
      await api("jobs/" + encodeURIComponent(control.dataset.id) + "/cancel", {
        method: "POST",
        body: "{}",
      });
      toast("正在取消导入，已完成文件会保留");
    } else if (action === "job-result")
      showJobResult(state.jobs.get(control.dataset.id));
  } catch (error) {
    if (action.startsWith("template-") && state.dialog?.templateMeta) {
      $("dialog-error").textContent = errorText(error);
      $("dialog-error").hidden = false;
    } else toast(errorText(error), true);
  }
});

boot();
