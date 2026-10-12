"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { conferences: [], settings: null, jobs: [], selected: null, page: 1, editing: null, view: "library", busy: false, fieldSave: null, conferenceRevision: 0, paperRequest: 0, zoteroList: null, zoteroPaper: null, tagEstimateRequest: 0, tagSelectionKey: null, tagSelectionIds: [], tagSelectionStats: null, pendingTag: null, confirmedTag: null, pendingPromptTest: null, filterDomainIds: [], domainFilterKey: null, dismissedProgress: JSON.parse(localStorage.getItem("research-tagger-dismissed-progress") || "{}") };
const number = (value) => new Intl.NumberFormat().format(value);
const money = (value) => "$" + value.toFixed(6);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
/** Convert known upstream failures into actionable messages without hiding unknown details. */
const friendlyError = (value) => {
  const message = String(value ?? "");
  if (/\b529\b/.test(message) && message.includes("api.typesafe.ai")) {
    return "TypeSafe is temporarily overloaded (HTTP 529). Tagging continues unless five requests fail consecutively. Wait a few minutes, then use Retry failed; saved results and untouched papers will be skipped.";
  }
  return message;
};
const current = () => state.conferences.find((item) => item.id === state.selected);
const running = () => state.jobs.find((job) => ["queued", "running"].includes(job.status));
const activeDomains = (conference = current()) => {
  const ids = conference?.stats.domain_ids || state.settings.tagging.domains.map((domain) => domain.id);
  return state.settings.tagging.domains.filter((domain) => ids.includes(domain.id));
};

// Selector → plain-text help. Used by static controls and dynamically added forms.
const FIELD_HELP = {
  "#api-key": "Your TypeSafe API key authorizes paid tagging requests. It stays in server memory until restart. Scraping and cost estimates do not need a key.",
  "#model-name": "The model used to classify papers. Enter an available TypeSafe model name. Changing it makes previous tags outdated; check the price for the new model too.",
  "#parallel-requests": "Maximum simultaneous requests during conference tagging (1–16; default 4). Each request sends one paper with all selected tags. Higher values may increase speed and rate-limit errors. Stop waits for in-flight requests to finish and saves their results. Prompt tests remain sequential. This changes speed, not the prompt or estimated token cost.",
  "#tag-threshold": "The minimum probability needed to assign a tag. For example, 0.70 requires at least 70%. Higher values assign fewer tags. Changing this reuses saved probabilities without calling the model again.",
  "#token-price": "The price in US dollars for one million input tokens. It is used for cost estimates and recorded costs. Update it to match TypeSafe’s price for your chosen model.",
  "#pricing-date": "The date you last checked the model’s price. This is a reminder for you; prices are not updated automatically.",
  "#prompt-question": "The shared yes/no question sent to the model for every tag, together with that tag’s name, definition, and the selected paper text. {fields} inserts the selected sections. Saving changes updates estimates and can make previous tags outdated.",
  "#prompt-test-conference": "Choose the saved conference whose complete papers will provide the test sample. The test uses that conference’s selected content fields.",
  "#prompt-test-count": "Choose between 1 and 10 complete papers. The app uses the first available papers in stable library order so repeated tests are comparable.",
  ".domain-label": "The readable name of this research tag, such as World models. It appears on paper tags and is sent to the model alongside the definition.",
  ".domain-id": "An automatically assigned identifier that connects this tag to its saved results. It is read-only; changing the tag name leaves this identifier unchanged.",
  ".domain-description": "Explain what this tag means and which papers should belong to it. Add examples of what to include or exclude. The model uses this definition to judge papers; broader definitions usually admit more papers.",
  "#conference-name": "Choose ICML, ECCV, ICLR, CVPR, ICCV, or NeurIPS. The app uses that conference’s official paper site. Once papers are saved, add a separate conference to collect another edition.",
  "#conference-year": "Choose an edition from 2000 through the current year. ECCV lists even years and ICCV lists odd years. An offered year is not a guarantee that its paper index is published; discovery checks availability.",
  "#conference-url": "The official paper index for the selected conference and year, filled in automatically. Discovery reads titles and paper links here. This address cannot be edited.",
  "#field-selection": "Choose the text sent for tagging. Title and Abstract are selected by default. Changes save automatically; keep at least one section checked. Introduction requires downloading arXiv TeX and extracting its introduction. Scraping always collects titles, abstracts, and PDF links. Changing text sections updates readiness and estimates, and can make saved tags outdated.",
  "#paper-search": "Search saved paper titles. This also selects papers for Zotero export across all pages. Conference text scraping and tagging use their own controls.",
  "#status-filter": "Show papers by collection or tagging status. Needs attention means a selected missing text field has an error. This also filters Zotero export; PDF-link errors appear on the paper but do not change its text status.",
  "#tag-selection": "Choose the tags to apply to conference papers. Search keeps your checked choices. Each selected tag adds its name and definition to the shared prompt, and the estimate updates for this selection. At least one tag is required. This selection is separate from the paper-list filter.",
  "#domain-filter": "Open the dropdown and check any tags to include. Search narrows the choices without removing selections. Papers must match ALL checked tags at your probability threshold. Clear all shows every tag. Zotero export uses the same filter; this does not change the tags sent to the model.",
  "#paper-arxiv-id": "The arXiv identifier for this paper, such as 2403.12345v1. Saving clears its stored introduction. The next scrape verifies the title and retrieves that version’s TeX source.",
};
let fieldHelpSequence = 0;
let activeFieldHelp = null;

/** Hide the open help bubble and reset its trigger's accessibility state. */
function closeFieldHelp() {
  if (!activeFieldHelp) return;
  activeFieldHelp.button.setAttribute("aria-expanded", "false");
  activeFieldHelp.tooltip.hidden = true;
  activeFieldHelp = null;
}

/** Show one plain-text tooltip, keeping it inside the viewport and any open dialog. */
function showFieldHelp(button, tooltip, group) {
  if (activeFieldHelp?.button === button) return;
  closeFieldHelp();
  activeFieldHelp = {button, tooltip, group, pinned: false};
  button.setAttribute("aria-expanded", "true");
  const dialog = group.closest("dialog"), bounds = dialog ? dialog.getBoundingClientRect() : {left:0, right:document.documentElement.clientWidth, top:0, bottom:window.innerHeight};
  const trigger = button.getBoundingClientRect(), container = group.getBoundingClientRect();
  tooltip.style.width = `${Math.min(352, bounds.right - bounds.left - 32)}px`;
  tooltip.style.left = "0px";
  tooltip.style.top = `${trigger.bottom - container.top + 6}px`;
  tooltip.hidden = false;
  const box = tooltip.getBoundingClientRect();
  tooltip.style.left = `${Math.max(bounds.left + 16, Math.min(box.left, bounds.right - box.width - 16)) - container.left}px`;
  if (box.bottom > Math.min(bounds.bottom, window.innerHeight) - 12 && trigger.top - box.height - 6 >= Math.max(bounds.top, 0) + 12) {
    tooltip.style.top = `${trigger.top - container.top - box.height - 6}px`;
  }
}

/** Add label-adjacent help buttons to controls in an Element or Document; safe to repeat. */
function addFieldHelp(root = document) {
  for (const [selector, description] of Object.entries(FIELD_HELP)) {
    root.querySelectorAll(selector).forEach((field) => {
      if (field.dataset.helpAttached) return;
      field.dataset.helpAttached = "true";
      const id = `field-help-${++fieldHelpSequence}`;
      if (!field.id) field.id = `${id}-control`;
      let label = field.closest("label");
      const group = document.createElement("div");
      group.className = `help-field ${label?.className || ""}`;
      if (label) label.replaceWith(group);
      else {
        field.replaceWith(group);
        label = document.createElement("label");
        label.textContent = field.getAttribute("aria-label");
      }
      label.htmlFor = field.id;
      const checkbox = field.type === "checkbox";
      if (!checkbox) field.remove();
      else group.classList.add("checkbox-help");
      const heading = document.createElement("div"); heading.className = "field-heading";
      const button = document.createElement("button"); button.type = "button"; button.className = "field-help-button"; button.textContent = "?";
      button.setAttribute("aria-label", `Help: ${label.textContent.trim()}`);
      button.setAttribute("aria-describedby", id); button.setAttribute("aria-expanded", "false");
      const tooltip = document.createElement("span"); tooltip.id = id; tooltip.className = "field-help-text"; tooltip.setAttribute("role", "tooltip"); tooltip.hidden = true; tooltip.textContent = description;
      field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"), id].filter(Boolean).join(" "));
      heading.append(label, button); group.append(heading);
      if (!checkbox) group.append(field);
      group.append(tooltip);
      button.addEventListener("pointerenter", () => showFieldHelp(button, tooltip, group));
      button.addEventListener("focus", () => showFieldHelp(button, tooltip, group));
      button.addEventListener("click", () => {
        if (activeFieldHelp?.button === button && activeFieldHelp.pinned) closeFieldHelp();
        else { showFieldHelp(button, tooltip, group); activeFieldHelp.pinned = true; }
      });
      button.addEventListener("blur", () => { if (activeFieldHelp?.button === button) closeFieldHelp(); });
      group.addEventListener("pointerleave", () => {
        if (activeFieldHelp?.button === button && !activeFieldHelp.pinned && document.activeElement !== button) closeFieldHelp();
      });
    });
  }
}
document.addEventListener("click", (event) => { if (activeFieldHelp && !activeFieldHelp.group.contains(event.target)) closeFieldHelp(); });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && activeFieldHelp) { event.preventDefault(); closeFieldHelp(); }
});
window.addEventListener("resize", closeFieldHelp);

async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, { method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function notice(message, error = false) {
  $("#notice").textContent = message; $("#notice").className = error ? "error" : ""; $("#notice").hidden = false;
}
async function refresh() {
  if (state.busy) return;
  state.busy = true;
  const revision = state.conferenceRevision;
  try {
    const [data, settings] = await Promise.all([api("/conferences"), api("/settings")]);
    if (state.fieldSave || revision !== state.conferenceRevision) return;
    state.conferences = data.conferences; state.jobs = data.jobs; state.settings = settings;
    if (!state.selected && state.conferences.length) state.selected = state.conferences[0].id;
    render();
    if (current() && state.view === "library") await loadPapers();
  } catch (error) { notice(error.message, true); }
  finally { state.busy = false; }
}
function render() {
  $("#conference-nav").innerHTML = state.conferences.map((item) => `<button class="conference-nav-item ${item.id === state.selected ? "active" : ""}" data-conference="${item.id}" ${item.id === state.selected ? 'aria-current="page"' : ""}><span>${escapeHTML(item.config.name)} ${item.config.year}</span><small>${number(item.stats.total)}</small></button>`).join("");
  $("#key-status").textContent = state.settings.api_key_configured ? "key configured" : "key not set";
  $("#empty-library").hidden = !!current(); $("#conference-content").hidden = !current();
  const conf = current(); if (!conf) return;
  const stats = conf.stats, active = running();
  $("#conference-title").textContent = `${conf.config.name} ${conf.config.year}`;
  $("#conference-meta").textContent = conf.discovered_at ? `Index checked ${new Date(conf.discovered_at).toLocaleString()} · ${conf.config.source === "virtual" ? "Official conference site" : conf.config.source.toUpperCase() + " proceedings"}` : "Scrape papers to collect this conference.";
  const cards = [["Indexed papers", stats.total, "In this edition"], ["Text ready", stats.scraped, "Selected text complete"], ["Text errors", stats.failed, "Need attention"], ["Papers without PDF links", stats.missing_pdf, "Collected by Scrape papers"]];
  if (conf.config.fields.includes("introduction")) cards.push(["Introductions", stats.introductions, "From arXiv TeX"]);
  $("#scrape-stats").innerHTML = cards.map(([label, value, hint], index) => `<div class="stat ${index === 1 ? "emphasis" : ""}"><div class="stat-label">${label}</div><div class="stat-value">${conf.discovered_at ? number(value) : "—"}</div><small>${hint}</small></div>`).join("");
  $("#coverage-text").textContent = conf.discovered_at ? `${stats.total ? Math.round(stats.scraped / stats.total * 100) : 0}% text ready` : "Scraping will first read the paper index.";
  $("#coverage-bar").innerHTML = [[stats.scraped,"blue"],[stats.pending,"gray"],[stats.failed,"red"]].map(([value,color]) => `<span class="${color}" style="width:${stats.total ? value / stats.total * 100 : 0}%"></span>`).join("");
  $("#coverage-bar").setAttribute("aria-label", `${stats.scraped} complete, ${stats.pending} pending, ${stats.failed} failed`);
  $("#scrape-summary").textContent = conf.discovered_at ? `${number(stats.scraped)} / ${number(stats.total)} text ready · ${number(stats.failed)} text errors · ${number(stats.missing_pdf)} missing PDF links` : "Scrape papers to get started";
  renderFieldSelection();
  ensureTagSelection();
  syncTagSelectionStats();
  renderRunControls();
  renderDomainFilter();
  const collectionJob = state.jobs.find((job) => job.conference_id === conf.id && job.kind !== "tag");
  const tagJob = state.jobs.find((job) => job.conference_id === conf.id && job.kind === "tag");
  if (collectionJob && ["running", "queued"].includes(collectionJob.status)) $("#scrape-summary").textContent += collectionJob.stage === "discovering" ? " · Reading paper index…" : " · Collecting missing content…";
  renderJobProgress($("#job-progress"), collectionJob, false);
  renderJobProgress($("#tag-job-progress"), tagJob, true);
}

/** Store one dismissed job ID per conference and job group; never reset job data. */
function setProgressDismissed(jobId, dismissed) {
  const job = state.jobs.find((item) => item.id === jobId);
  if (!job || ["queued", "running"].includes(job.status)) return;
  const key = `${job.conference_id}:${job.kind === "tag" ? "tag" : "collection"}`;
  if (dismissed) state.dismissedProgress[key] = job.id;
  else delete state.dismissedProgress[key];
  localStorage.setItem("research-tagger-dismissed-progress", JSON.stringify(state.dismissedProgress));
  render();
  $(`[data-${dismissed ? "restore" : "dismiss"}-progress="${job.id}"]`).focus();
}

/** Render the latest job or a compact restore control for dismissed finished progress. */
function renderJobProgress(element, job, tagging) {
  element.hidden = !job;
  if (!job) { element.innerHTML = ""; return; }
  const activeJob = ["queued","running"].includes(job.status);
  const progressKey = `${job.conference_id}:${tagging ? "tag" : "collection"}`;
  if (!activeJob && state.dismissedProgress[progressKey] === job.id) {
    element.className = "job-progress-dismissed";
    element.innerHTML = `<button type="button" data-restore-progress="${job.id}">Show last ${tagging ? "tagging" : "collection"} run</button>`;
    return;
  }
  const failed = ["failed","interrupted"].includes(job.status) || job.failed > 0;
  element.className = `job-progress${tagging ? " tagging-job-progress" : ""}${failed ? " failed" : ""}`;
  let detail, progress = "";
  if (tagging || ["scrape", "pdf_links"].includes(job.kind)) {
    const processed = Math.min(job.total, job.completed + job.failed);
    const percent = job.total ? Math.round(processed / job.total * 100) : 0;
    detail = job.total
      ? `${number(processed)} of ${number(job.total)} processed · ${number(job.completed)} ${tagging ? "evaluated" : job.kind === "scrape" ? "collected" : "links found"} · ${number(job.failed)} failed · ${percent}%`
      : activeJob ? (tagging ? "Preparing papers for tagging…" : "Preparing paper collection…") : "No papers were processed.";
    progress = `<progress max="${Math.max(job.total, 1)}" ${job.total || !activeJob ? `value="${processed}"` : ""} aria-label="${tagging ? "Tagging" : job.kind === "scrape" ? "Scraping" : "PDF link collection"} progress">${percent}%</progress>`;
  } else {
    detail = job.kind === "discover"
      ? (activeJob ? "Reading the conference paper index…" : job.status === "completed" ? `${number(job.completed)} papers indexed` : "Paper index discovery did not complete. No paper totals are available for this run.")
      : ["arxiv_links", "pdf_links"].includes(job.kind) ? `${number(job.completed)} links found · ${number(job.failed)} failed · ${number(job.completed + job.failed)} of ${number(job.total)} processed`
      : `${number(job.completed)} succeeded · ${number(job.failed)} failed · ${number(job.total)} in this run`;
  }
  if (job.kind === "scrape" && job.stage === "discovering") {
    detail = activeJob ? "Step 1 of 2 · Reading the conference paper index…"
      : job.status === "cancelled" ? "Stopped while reading the paper index; saved papers were kept."
      : "Could not refresh the paper index. Saved papers were kept; no content scraping started.";
    progress = activeJob ? '<progress aria-label="Paper discovery progress"></progress>' : "";
  } else if (job.kind === "scrape" && job.stage === "scraping") {
    detail = activeJob ? `Step 2 of 2 · ${detail}` : job.status === "completed" && !job.total
      ? "Paper index refreshed. All papers already have the requested content and PDF links." : detail;
  }
  if (!activeJob) progress = "";
  const statusLabel = job.status === "completed" ? job.failed ? `Finished with ${number(job.failed)} errors` : "Finished" : job.status;
  const jobLabel = tagging ? "tagging" : job.kind === "arxiv_links" ? "arXiv links" : job.kind === "pdf_links" ? "PDF links" : job.kind;
  const action = activeJob
    ? `<button data-stop="${job.id}" ${job.cancel_requested ? "disabled" : ""}>Stop</button>`
    : tagging && job.failed ? `<button data-retry-tag class="primary" ${state.settings.api_key_configured ? "" : "disabled"}>Retry ${number(job.failed)} failed</button>` : "";
  const dismiss = activeJob ? "" : `<button type="button" class="icon-button dismiss-progress" data-dismiss-progress="${job.id}" aria-label="Dismiss ${tagging ? "tagging" : "collection"} progress" title="Hide this run’s progress and messages">×</button>`;
  element.innerHTML = `<div class="job-progress-copy"><strong>${escapeHTML(jobLabel)} · ${escapeHTML(statusLabel)}</strong><p>${escapeHTML(detail)}${job.cancel_requested && activeJob ? " · stopping; waiting for in-flight requests" : ""}</p>${progress}${job.error ? `<p>${escapeHTML(friendlyError(job.error))}</p>` : ""}</div>${action}${dismiss}`;
}
/** Initialize tag choices when the conference or saved tag definitions change. */
function ensureTagSelection() {
  const conf = current(), domains = state.settings.tagging.domains;
  const key = `${conf.id}:${JSON.stringify(domains)}`;
  if (state.tagSelectionKey === key) return;
  const selected = new Set(conf.stats.domain_ids || domains.map((domain) => domain.id));
  state.tagSelectionKey = key;
  state.tagSelectionIds = domains.filter((domain) => selected.has(domain.id)).map((domain) => domain.id);
  state.tagSelectionStats = conf.stats;
  state.pendingTag = state.tagSelectionIds.length && conf.stats.estimate_signature
    ? {conferenceId:conf.id,request:{kind:"tag",limit:null,domain_ids:state.tagSelectionIds,estimate_signature:conf.stats.estimate_signature}}
    : null;
  closeTagSelection();
  $("#tag-selection-search").value = "";
  $("#tag-domain-options").innerHTML = domains.map((domain) => `<label class="domain-filter-option"><input type="checkbox" name="tag-domain" value="${escapeHTML(domain.id)}" ${selected.has(domain.id) ? "checked" : ""}><span title="${escapeHTML(domain.label)}">${escapeHTML(domain.label)}</span></label>`).join("");
  $("#tag-selection-error").textContent = !conf.stats.ready_to_tag
    ? "All complete papers already have current results for this tag selection."
    : state.settings.api_key_configured ? "" : "Connect a TypeSafe API key in Settings before tagging.";
  renderTagPicker();
}

/** Refresh the selected-tag estimate from conference statistics when it is the saved selection. */
function syncTagSelectionStats() {
  const conf = current();
  const persisted = conf.stats.domain_ids || state.settings.tagging.domains.map((domain) => domain.id);
  if (JSON.stringify(persisted) !== JSON.stringify(state.tagSelectionIds)) return;
  state.tagSelectionStats = conf.stats;
  state.pendingTag = conf.stats.estimate_signature
    ? {conferenceId:conf.id,request:{kind:"tag",limit:null,domain_ids:[...state.tagSelectionIds],estimate_signature:conf.stats.estimate_signature}}
    : null;
}

/** Update the tag dropdown summary from selected IDs without rebuilding focused choices. */
function renderTagPicker() {
  const count = state.tagSelectionIds.length;
  const tag = state.settings.tagging.domains.find((item) => item.id === state.tagSelectionIds[0]);
  const label = count === 1 ? tag.label : count ? "Tags" : "Tags to apply";
  $("#tag-selection-label").textContent = label;
  $("#tag-selection-count").textContent = `${number(count)} selected`;
  $("#tag-selection-count").hidden = count < 2;
  $("#tag-selection").classList.toggle("has-selection", count > 0);
  $("#tag-selection").setAttribute("aria-label", `Tags to apply: ${count > 1 ? `${count} selected` : label}`);
  $("#clear-tag-selection").disabled = !count;
  filterTagOptions();
}

/** Search tag choices without changing the selected tags or estimate. */
function filterTagOptions() {
  const query = $("#tag-selection-search").value.trim().toLocaleLowerCase();
  const rows = $$("#tag-domain-options .domain-filter-option");
  rows.forEach((row) => row.hidden = !row.textContent.toLocaleLowerCase().includes(query));
  $("#tag-selection-empty").hidden = rows.some((row) => !row.hidden);
}

/** Close the tagging dropdown, optionally returning keyboard focus to its button. */
function closeTagSelection(restoreFocus = false) {
  $("#tag-selection-panel").hidden = true;
  $("#tag-selection").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("#tag-selection").focus();
}

/** Render counts for the tag definitions included in the current estimate. */
function renderTagDomainStats(stats) {
  const selected = new Set(state.tagSelectionIds);
  const domains = state.settings.tagging.domains.filter((domain) => selected.has(domain.id));
  $("#domain-stats").innerHTML = domains.map((domain) => {
    const count = stats?.domain_counts?.[domain.id] || 0;
    return `<div class="domain-stat"><span>${escapeHTML(domain.label)}</span><div class="domain-track"><span style="width:${stats?.tagged ? count / stats.tagged * 100 : 0}%"></span></div><strong>${number(count)}</strong></div>`;
  }).join("");
}

/** Render conference-wide scrape/tag availability and the checked-tag cost estimate. */
function renderRunControls() {
  const conf = current(), stats = conf.stats, active = running();
  const selectionCurrent = state.tagSelectionKey?.startsWith(`${conf.id}:`);
  const tagStats = selectionCurrent ? state.tagSelectionStats : stats;
  $("#scrape-button").disabled = !!state.fieldSave || !!active;
  $("#tag-button").disabled = !!state.fieldSave || !!active || !conf.discovered_at || !tagStats?.ready_to_tag || !state.pendingTag || !state.settings.api_key_configured;
  $("#scrape-button").textContent = "Scrape papers";
  $("#tag-button").textContent = "Tag papers";
  $("#tagged-count").textContent = tagStats ? number(tagStats.tagged) : "—";
  $("#tag-ready-count").textContent = tagStats ? number(tagStats.ready_to_tag) : "—";
  $("#tag-token-count").textContent = tagStats ? `${number(tagStats.input_tokens)} tokens` : "—";
  $("#estimated-cost").textContent = tagStats ? money(tagStats.estimated_cost) : "—";
  $("#tag-summary").textContent = tagStats ? `${number(tagStats.tagged)} evaluated · ${number(tagStats.ready_to_tag)} awaiting tagging · ${money(tagStats.estimated_cost)} estimated` : "Choose tags to see the estimate";
  if (active?.conference_id === conf.id && active.kind === "tag") $("#tag-summary").textContent += " · Tagging in progress";
  renderTagDomainStats(tagStats);
}
/** Render persistent checkbox choices without replacing focused rows during polling. */
function renderDomainFilter() {
  const domains = activeDomains();
  const available = new Set(domains.map((domain) => domain.id));
  state.filterDomainIds = state.filterDomainIds.filter((id) => available.has(id));
  const key = JSON.stringify(domains.map(({id, label}) => ({id, label})));
  if (state.domainFilterKey !== key) {
    state.domainFilterKey = key;
    $("#domain-filter-options").innerHTML = domains.map((domain) => `<label class="domain-filter-option"><input type="checkbox" name="filter-domain" value="${escapeHTML(domain.id)}"><span>${escapeHTML(domain.label)}</span></label>`).join("");
  }
  $$("input[name=filter-domain]").forEach((input) => input.checked = state.filterDomainIds.includes(input.value));
  const count = state.filterDomainIds.length;
  const label = count === 1 ? domains.find((domain) => domain.id === state.filterDomainIds[0]).label : count ? "Tags" : "All tags";
  $("#domain-filter-label").textContent = label;
  $("#domain-filter-count").textContent = `${number(count)} selected`;
  $("#domain-filter-count").hidden = count < 2;
  $("#domain-filter").classList.toggle("has-selection", count > 0);
  $("#domain-filter").setAttribute("aria-label", `Filter papers by tags: ${count > 1 ? `${count} selected` : label}`);
  $("#clear-domain-filter").disabled = !count;
  filterDomainOptions();
}

/** Search only the choices; checked domains remain active even when hidden. */
function filterDomainOptions() {
  const query = $("#domain-filter-search").value.trim().toLocaleLowerCase();
  const rows = $$("#domain-filter-options .domain-filter-option");
  rows.forEach((row) => row.hidden = !row.textContent.toLocaleLowerCase().includes(query));
  $("#domain-filter-empty").hidden = rows.some((row) => !row.hidden);
}

/** Close the picker, optionally restoring keyboard focus to its trigger. */
function closeDomainFilter(restoreFocus = false) {
  $("#domain-filter-panel").hidden = true;
  $("#domain-filter").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("#domain-filter").focus();
}

/** Publish the active dialog/list context for the local Zotero translator.
 * Context: {version:1, mode:"single"|"multiple", endpoint:string, count:number}|null.
 * Notify only on changes, so polling does not repeatedly trigger detection.
 */
function updateZoteroContext() {
  const list = state.zoteroList?.endpoint.startsWith(`/api/conferences/${state.selected}/`) ? state.zoteroList : null;
  const context = state.view === "library" ? (state.zoteroPaper || list) : null;
  const node = $("#research-tagger-zotero"), value = JSON.stringify(context);
  if (node.textContent === value) return;
  node.textContent = value;
  document.dispatchEvent(new Event("ZoteroItemUpdated", {bubbles:true}));
}

async function loadPapers() {
  const requestId = ++state.paperRequest;
  const params = new URLSearchParams({search: $("#paper-search").value, status: $("#status-filter").value});
  for (const id of state.filterDomainIds) params.append("domain", id);
  const selected = state.selected;
  const endpoint = `/api/conferences/${selected}/zotero?${params}`;
  if (state.zoteroList?.endpoint !== endpoint) {
    state.zoteroList = null;
    updateZoteroContext();
  }
  const data = await api(`/conferences/${selected}/papers?${params}&page=${state.page}`);
  if (selected !== state.selected || requestId !== state.paperRequest) return;
  state.zoteroList = data.count ? {version:1, mode:"multiple", endpoint, count:data.count} : null;
  updateZoteroContext();
  renderRunControls();
  $("#papers-count").textContent = `${number(data.count)} papers in this view`;
  const exportLink = $("#export-zotero");
  exportLink.href = data.count ? `/api/conferences/${selected}/papers.ris?${params}` : "";
  exportLink.classList.toggle("disabled", !data.count);
  exportLink.setAttribute("aria-disabled", String(!data.count));
  $("#paper-empty").hidden = data.count > 0;
  $("#paper-rows").innerHTML = data.papers.map((paper) => {
    const domains = paper.probabilities ? activeDomains().filter((domain) => paper.probabilities[domain.id] >= state.settings.tagging.threshold) : [];
    const missing = [];
    if (current().config.fields.includes("abstract") && !paper.has_abstract) missing.push("Abstract missing");
    if (current().config.fields.includes("introduction") && !paper.has_introduction) missing.push("Introduction missing");
    if (!paper.pdf_url) missing.push("PDF link missing");
    const collection = missing.length ? missing.map(text => `<span class="collection-missing">${text}</span>`).join("") : '<span class="muted">Ready</span>';
    const error = paper.abstract_error || paper.introduction_error || paper.pdf_error || paper.tag_error || paper.context_error;
    return `<tr><td><button class="paper-title" data-paper="${paper.id}">${escapeHTML(paper.title)}</button>${paper.pdf_url ? ` <a href="${escapeHTML(paper.pdf_url)}" target="_blank" rel="noreferrer">PDF ↗</a>` : ""}${error ? `<p class="paper-error">${escapeHTML(friendlyError(error))}</p>` : ""}</td><td>${collection}</td><td><span class="badge ${paper.status}">${paper.status === "failed" ? "Needs attention" : paper.status === "tagged" ? "Evaluated" : paper.status === "scraped" ? "Awaiting evaluation" : "Pending"}</span></td><td>${domains.map((domain) => `<span class="tag" title="${Math.round(paper.probabilities[domain.id] * 100)}% probability">${escapeHTML(domain.label)}</span>`).join("") || `<span class="muted">${paper.probabilities ? "No matching tags" : "—"}</span>`}</td></tr>`;
  }).join("");
  $("#page-label").textContent = `Page ${state.page} of ${Math.max(1,Math.ceil(data.count / 30))}`;
  $("#previous-page").disabled = state.page <= 1; $("#next-page").disabled = state.page * 30 >= data.count;
}
function setView(view) {
  closeFieldHelp();
  closeTagSelection();
  closeDomainFilter();
  closeFieldSelection();
  $("#field-selection-status").textContent = "";
  state.view = view;
  updateZoteroContext();
  $("#dashboard").hidden = view !== "library"; $("#settings-view").hidden = view !== "settings";
  if (view === "settings") renderSettings();
  window.scrollTo?.({top:0});
}
function renderSettings() {
  if (!state.settings) return;
  $("#settings-conferences").innerHTML = state.conferences.map((conf) => `<div class="conference-setting"><div><strong>${escapeHTML(conf.config.name)} ${conf.config.year}</strong><small>${conf.config.fields.join(" + ")}</small></div><button data-edit-conference="${conf.id}">${conf.discovered_at ? "Conference details" : "Edit edition"}</button></div>`).join("") || '<p class="muted">No conferences added yet.</p>';
  $("#connection-state").textContent = state.settings.api_key_configured ? "A TypeSafe API key is configured. Connection is verified when you tag." : "No TypeSafe API key is configured. Scraping and estimates work without one.";
  const config = state.settings.tagging;
  $("#model-name").value = config.model; $("#tag-threshold").value = config.threshold; $("#token-price").value = config.price_per_million; $("#pricing-date").value = config.pricing_date;
  $("#parallel-requests").value = config.parallel_requests;
  $("#prompt-question").value = config.prompt.question;
  $("#tagging-form-error").textContent = "";
  $("#domain-editors").innerHTML = ""; config.domains.forEach(addDomainEditor);
  const selectedTestConference = state.conferences.some((conf) => conf.id === state.selected) ? state.selected : state.conferences[0]?.id;
  $("#prompt-test-conference").innerHTML = state.conferences.map((conf) => `<option value="${conf.id}">${escapeHTML(conf.config.name)} ${conf.config.year}</option>`).join("");
  if (selectedTestConference) $("#prompt-test-conference").value = String(selectedTestConference);
  $("#preview-prompt-test").disabled = !state.conferences.length;
  resetPromptTestOutput();
}

/** Read and normalize the current settings form, including unsaved prompt/domain edits. */
function draftTaggingConfig() {
  const domains = $$(".domain-editor").map((editor) => ({
    id:editor.querySelector(".domain-id").value.trim(),
    label:editor.querySelector(".domain-label").value.trim(),
    description:editor.querySelector(".domain-description").value.trim(),
  }));
  return {domains,prompt:{question:$("#prompt-question").value.trim()},model:$("#model-name").value.trim(),
    parallel_requests:Number($("#parallel-requests").value),threshold:Number($("#tag-threshold").value),price_per_million:Number($("#token-price").value),pricing_date:$("#pricing-date").value};
}

/** Clear a prompt-test review/result when any input that produced it changes. */
function resetPromptTestOutput() {
  state.pendingPromptTest = null;
  $("#prompt-test-error").textContent = "";
  $("#prompt-test-results").hidden = true;
  $("#prompt-test-results").innerHTML = "";
}

/** Require the prompt-test number spinner to contain a whole number from 1 through 10. */
function promptTestLimit() {
  const limit = Number($("#prompt-test-count").value);
  if (!Number.isInteger(limit) || limit < 1 || limit > 10) throw new Error("Choose between 1 and 10 papers for the prompt test.");
  return limit;
}

/** Display transient per-paper probabilities and actual model usage from a prompt test. */
function renderPromptTestResults(data, tagging) {
  const domains = new Map(tagging.domains.map((domain) => [domain.id, domain]));
  const papers = data.results.map((paper) => {
    const scores = Object.entries(paper.probabilities).map(([id, probability]) => {
      const domain = domains.get(id), matched = probability >= tagging.threshold;
      return `<span class="prompt-test-score ${matched ? "matched" : ""}"><strong>${escapeHTML(domain?.label || id)}</strong> ${Math.round(probability * 100)}%${matched ? " · match" : ""}</span>`;
    }).join("");
    return `<article class="prompt-test-paper"><h4>${escapeHTML(paper.title)}</h4><div class="prompt-test-scores">${scores}</div><small>${escapeHTML(paper.model)} · ${number(paper.input_tokens)} input tokens · ${money(paper.cost_usd)}</small></article>`;
  }).join("");
  $("#prompt-test-results").innerHTML = `<div class="prompt-test-usage"><strong>Test complete</strong><span>${number(data.actual_input_tokens)} input tokens · ${number(data.actual_output_tokens)} output tokens · ${money(data.actual_cost)} actual input cost</span></div>${papers}`;
  $("#prompt-test-results").hidden = false;
}
/** Generate a unique stable tag ID, retaining saved IDs even if an editor was removed. */
function nextTagId() {
  const used = new Set([...state.settings.tagging.domains.map(domain => domain.id), ...$$(".domain-id").map(input => input.value)]);
  let number = 1;
  while (used.has(`tag_${number}`)) number++;
  return `tag_${number}`;
}
/** Add a tag editor; generated and existing IDs are read-only under Advanced. */
function addDomainEditor(domain = {id:nextTagId(),label:"",description:""}) {
  const editor = document.createElement("div"); editor.className = "domain-editor";
  editor.innerHTML = `<label>Tag name<input class="domain-label" value="${escapeHTML(domain.label)}" required></label><label>Definition and inclusion criteria<textarea class="domain-description" required>${escapeHTML(domain.description)}</textarea></label><details class="tag-advanced"><summary>Advanced</summary><label>Stable ID<input class="domain-id" value="${escapeHTML(domain.id)}" readonly></label></details><button type="button" data-remove-domain>Remove tag</button>`;
  $("#domain-editors").append(editor);
  addFieldHelp(editor);
}
/** Reveal invalid controls inside collapsed advanced settings before showing validation. */
function validateTaggingForm() {
  const form = $("#tagging-form");
  for (const input of form.querySelectorAll(":invalid")) {
    let parent = input.parentElement;
    while (parent && parent !== form) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
  }
  return form.reportValidity();
}
/** Fill the permitted edition years from the API, retaining a valid selected year. */
function updateConferenceYears(year = Number($("#conference-year").value)) {
  const option = state.settings.conference_options.find((item) => item.name === $("#conference-name").value);
  $("#conference-year").innerHTML = option.years.map((value) => `<option value="${value}">${value}</option>`).join("");
  if (option.years.includes(year)) $("#conference-year").value = String(year);
  updateConferenceUrl();
}
/** Preview the official URL derived from the selected conference and year. */
function updateConferenceUrl() {
  const name = $("#conference-name").value, year = Number($("#conference-year").value);
  const option = state.settings.conference_options.find((item) => item.name === name);
  const saved = state.conferences.find((conf) => conf.id === state.editing);
  $("#conference-url").value = option.url_template.replace("{year}", year);
  $("#conference-edition-note").textContent = saved?.discovered_at ? "This edition already has an index. Add a new conference to collect a different edition." : name === "ECCV" ? "ECCV takes place in even years." : name === "ICCV" ? "ICCV takes place in odd years." : name === "NeurIPS" ? "Discovery finds the selected year’s main proceedings in the NeurIPS archive. Separate Creative AI volumes are excluded." : "Choose the conference edition you want to collect.";
}
/** Open the edition form for a saved numeric ID, or null to add a new edition. */
function openConference(id = null) {
  if (!state.settings) { notice("Conference options have not loaded yet. Try again after the connection succeeds.", true); return; }
  state.editing = id; $("#conference-form").reset(); $("#conference-form-error").textContent = "";
  $("#conference-form-title").textContent = id ? "Edit conference edition" : "Add conference";
  const conf = state.conferences.find((conf) => conf.id === id);
  $("#conference-name").innerHTML = state.settings.conference_options.map((item) => `<option value="${item.name}">${item.name}</option>`).join("");
  if (conf) $("#conference-name").value = conf.config.name;
  $("#conference-name").disabled = $("#conference-year").disabled = !!conf?.discovered_at;
  $("#save-conference").hidden = !!conf?.discovered_at;
  if (conf?.discovered_at) $("#conference-form-title").textContent = "Conference details";
  updateConferenceYears(conf ? conf.config.year : undefined);
  $("#conference-dialog").showModal();
}
/** Free collection actions always process all eligible conference papers. */
function jobRequest(kind) {
  return {kind, limit:null, estimate_signature:null};
}

/** Show the saved sections, retaining only this conference's pending edit during autosave. */
function renderFieldSelection() {
  const conf = current();
  const fields = state.fieldSave?.conferenceId === conf.id ? state.fieldSave.fields : conf.config.fields;
  const names = {title:"Title", abstract:"Abstract", introduction:"Introduction"};
  $$("input[name=field]").forEach((input) => {
    input.checked = fields.includes(input.value);
    input.disabled = !!state.fieldSave || !!running();
  });
  const count = fields.length, label = count === 1 ? names[fields[0]] : "Sections";
  $("#field-selection-label").textContent = label;
  $("#field-selection-count").textContent = `${number(count)} selected`;
  $("#field-selection-count").hidden = count < 2;
  $("#field-selection").classList.toggle("has-selection", count > 0);
  $("#field-selection").setAttribute("aria-label", `Text used for tagging: ${count > 1 ? `${count} selected` : label}`);
}

/** Close the text-section dropdown and optionally restore focus to its button. */
function closeFieldSelection(restoreFocus = false) {
  $("#field-selection-panel").hidden = true;
  $("#field-selection").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("#field-selection").focus();
}

/** Save one section change, block actions until saved, then recompute coverage and tag estimates. */
async function saveFieldSelection() {
  if (state.fieldSave || running()) { renderFieldSelection(); return; }
  const conf = current(), conferenceId = conf.id;
  const fields = $$("input[name=field]:checked").map((input) => input.value);
  if (!fields.length) {
    $("#field-selection-status").textContent = "Keep at least one text section selected.";
    renderFieldSelection();
    return;
  }
  state.fieldSave = {conferenceId, fields};
  state.conferenceRevision++;
  state.tagEstimateRequest++;
  $("#field-selection-status").textContent = "Saving text selection…";
  render();
  try {
    await api(`/conferences/${conferenceId}`, "PUT", {name:conf.config.name,year:conf.config.year,fields});
    const data = await api("/conferences");
    state.conferences = data.conferences; state.jobs = data.jobs;
    if (state.selected === conferenceId) $("#field-selection-status").textContent = "Saved automatically.";
  } catch (error) {
    if (state.selected === conferenceId) $("#field-selection-status").textContent = `Could not save text selection: ${error.message}`;
  } finally {
    state.fieldSave = null;
    state.conferenceRevision++;
    render();
  }
  if (state.selected === conferenceId && !running()) await updateTagSelectionEstimate();
  await loadPapers();
}

/** Return the domain IDs checked in the conference tagging box. */
function selectedTagIds() {
  return $$("input[name=tag-domain]:checked").map((input) => input.value);
}

/** Recalculate the paid conference estimate for the exact selected tag questions. */
async function updateTagSelectionEstimate() {
  const requestId = ++state.tagEstimateRequest, domainIds = selectedTagIds();
  state.tagSelectionIds = domainIds;
  state.tagSelectionStats = null;
  state.pendingTag = null;
  $("#tag-selection-error").textContent = "";
  renderTagPicker();
  renderRunControls();
  if (!domainIds.length) { $("#tag-selection-error").textContent = "Select at least one tag."; return; }
  try {
    const conferenceId = state.selected;
    const stats = await api(`/conferences/${conferenceId}/tag-estimate`, "POST", {domain_ids:domainIds});
    if (requestId !== state.tagEstimateRequest || conferenceId !== state.selected) return;
    state.tagSelectionStats = stats;
    state.pendingTag = {conferenceId,request:{kind:"tag",limit:null,domain_ids:domainIds,estimate_signature:stats.estimate_signature}};
    if (!stats.ready_to_tag) $("#tag-selection-error").textContent = "All complete papers already have current results for this tag selection.";
    else if (!state.settings.api_key_configured) $("#tag-selection-error").textContent = "Connect a TypeSafe API key in Settings before tagging.";
    renderRunControls();
  } catch (error) {
    if (requestId === state.tagEstimateRequest) { $("#tag-selection-error").textContent = error.message; renderRunControls(); }
  }
}

/** Open the final confirmation for the exact checked tags and estimate. */
async function openTagDialog(retryFailedOnly = false) {
  if (!state.pendingTag || !state.tagSelectionStats) throw new Error("Select tags and wait for the estimate before starting.");
  const labels = state.settings.tagging.domains.filter((domain) => state.tagSelectionIds.includes(domain.id)).map((domain) => domain.label);
  const stats = state.tagSelectionStats;
  const latest = state.jobs.find((job) => job.conference_id === state.selected && job.kind === "tag");
  state.confirmedTag = {conferenceId:state.pendingTag.conferenceId,request:{...state.pendingTag.request,retry_failed_only:retryFailedOnly}};
  $("#tag-confirmation").textContent = retryFailedOnly
    ? `Only the ${number(latest?.failed || 0)} papers with saved tagging errors will be retried for ${labels.join(", ")}. Papers already tagged and papers not previously attempted will be skipped.`
    : `${number(stats.ready_to_tag)} papers will be sent to ${state.settings.tagging.model} for ${labels.join(", ")}. Estimated input: ${number(stats.input_tokens)} tokens; estimated price: ${money(stats.estimated_cost)}.`;
  closeTagSelection();
  $("#tag-dialog").showModal();
}
/** Start the exact reviewed paid request, or a free collection request from the current controls. */
async function startJob(kind, reviewed = null) {
  if (state.fieldSave) throw new Error("Wait for the text selection to finish saving.");
  if (kind === "tag" && !reviewed) throw new Error("Review the tagging estimate before starting.");
  const id = reviewed ? reviewed.conferenceId : current().id;
  await api(`/conferences/${id}/jobs`, "POST", reviewed ? reviewed.request : jobRequest(kind));
  const label = {discover:"Paper discovery", scrape:"Scraping", tag:"Tagging"}[kind];
  notice(`${label} started. Progress is saved as each paper finishes.`);
  await refresh();
}
/** Render current saved scores, reading content and optional introduction repair controls. */
function paperDetailHTML(paper) {
  const config = state.conferences.find(conf => conf.id === paper.conference_id).config;
  const scores = paper.probabilities ? Object.entries(paper.probabilities).map(([id, probability]) => {
    const domain = state.settings.tagging.domains.find(item => item.id === id);
    const matched = probability >= state.settings.tagging.threshold;
    return `<span class="prompt-test-score ${matched ? "matched" : ""}"><strong>${escapeHTML(domain?.label || id)}</strong> ${Math.round(probability * 100)}% · ${matched ? "Match" : "Below threshold"}</span>`;
  }).join("") : '<p class="muted">No saved evaluation matches the current tags, prompt and text selection.</p>';
  const introSelected = config.fields.includes("introduction");
  const showIntro = introSelected || !!paper.introduction;
  const repair = `<details class="advanced-settings"><summary>${introSelected ? "Introduction source / repair" : "Advanced: introduction source"}</summary>${!introSelected ? '<p class="fine-print">Select Introduction in the conference workspace to collect it.</p>' : ""}${paper.arxiv_error ? `<p class="error-detail">${escapeHTML(paper.arxiv_error)}</p>` : ""}<form id="arxiv-form" data-id="${paper.id}" class="inline-form"><label>arXiv ID<input id="paper-arxiv-id" value="${escapeHTML(paper.arxiv_id || "")}" placeholder="2403.12345v1" required></label><button ${running() ? "disabled" : ""}>Save ID</button></form><p class="fine-print">Changing the ID clears the saved introduction. Scrape papers verifies the title and retrieves its TeX source.</p></details>`;
  return `<h2>${escapeHTML(paper.title)}</h2><div class="paper-detail-links"><a href="${escapeHTML(paper.source_url)}" target="_blank" rel="noreferrer">Conference paper ↗</a>${paper.pdf_url ? `<a href="${escapeHTML(paper.pdf_url)}" target="_blank" rel="noreferrer">Paper PDF ↗</a>` : '<span class="muted">No PDF link saved</span>'}${paper.arxiv_id ? `<a href="https://arxiv.org/abs/${escapeHTML(paper.arxiv_id)}" target="_blank" rel="noreferrer">arXiv ↗</a>` : ""}<button type="button" data-zotero-help>Zotero help</button></div><h3>Tag evaluation</h3><p class="fine-print">Match threshold: ${Math.round(state.settings.tagging.threshold * 100)}%${paper.estimated_tokens != null ? ` · ~${number(paper.estimated_tokens)} estimated input tokens` : ""}</p><div class="prompt-test-scores">${scores}</div>${paper.tag_error ? `<p class="error-detail">${escapeHTML(friendlyError(paper.tag_error))}</p>` : ""}<h3>Abstract</h3>${paper.abstract ? `<p class="paper-text">${escapeHTML(paper.abstract)}</p>` : '<p class="muted">Not scraped yet.</p>'}${paper.abstract_error ? `<p class="error-detail">${escapeHTML(paper.abstract_error)}</p>` : ""}${paper.pdf_error ? `<p class="error-detail">${escapeHTML(paper.pdf_error)}</p>` : ""}${showIntro ? `<h3>Introduction</h3>${paper.introduction ? `<p class="paper-text">${escapeHTML(paper.introduction)}</p>` : '<p class="muted">Not collected. Run Scrape papers.</p>'}${paper.introduction_error ? `<p class="error-detail">${escapeHTML(paper.introduction_error)}</p>` : ""}` : ""}${repair}`;
}
async function openPaper(id) {
  const paper = await api(`/papers/${id}`);
  $("#paper-detail").innerHTML = paperDetailHTML(paper);
  $("#paper-dialog").showModal();
  state.zoteroPaper = {version:1, mode:"single", endpoint:`/api/conferences/${paper.conference_id}/zotero?paper_id=${paper.id}`, count:1};
  updateZoteroContext();
  addFieldHelp($("#paper-detail"));
}
$("#paper-dialog").addEventListener("close", () => {
  state.zoteroPaper = null;
  updateZoteroContext();
});

function safely(action) {
  return async (event) => { try { await action(event); } catch (error) { notice(error.message, true); } };
}
document.addEventListener("click", safely(async (event) => {
  const target = event.target.closest("button"); if (!target) return;
  if (target.hasAttribute("data-zotero-help")) $("#zotero-dialog").showModal();
  if (target.dataset.close) $("#" + target.dataset.close).close();
  if (target.dataset.conference) {
    state.selected = Number(target.dataset.conference); state.page = 1;
    $("#paper-search").value = ""; $("#status-filter").value = "all"; state.filterDomainIds = [];
    $("#domain-filter-search").value = ""; closeDomainFilter();
    setView("library"); render(); await loadPapers();
  }
  if (target.dataset.editConference) openConference(Number(target.dataset.editConference));
  if (target.dataset.paper) await openPaper(Number(target.dataset.paper));
  if (target.dataset.dismissProgress) setProgressDismissed(target.dataset.dismissProgress, true);
  if (target.dataset.restoreProgress) setProgressDismissed(target.dataset.restoreProgress, false);
  if (target.dataset.stop) { await api(`/jobs/${target.dataset.stop}/cancel`, "POST", {}); await refresh(); }
  if (target.hasAttribute("data-retry-tag")) await openTagDialog(true);
  if (target.hasAttribute("data-remove-domain")) { target.closest(".domain-editor").remove(); resetPromptTestOutput(); }
}));
for (const selector of ["#add-conference", "#empty-add"]) $(selector).addEventListener("click", () => openConference());
for (const selector of ["#scraping-overview", "#tagging-overview"]) {
  const panel = $(selector), key = `research-tagger-panel-${selector.slice(1)}`;
  panel.open = localStorage.getItem(key) === "open";
  panel.addEventListener("toggle", () => localStorage.setItem(key, panel.open ? "open" : "closed"));
}
$("#settings-button").addEventListener("click", () => setView("settings"));
$("#back-library").addEventListener("click", safely(async () => { setView("library"); await refresh(); }));
$("#conference-year").addEventListener("change", updateConferenceUrl);
$("#conference-name").addEventListener("change", () => updateConferenceYears());
$("#conference-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const previous = state.conferences.find((conf) => conf.id === state.editing);
    const config = {name: $("#conference-name").value, year: Number($("#conference-year").value),
      fields: previous ? previous.config.fields : ["title", "abstract"]};
    const result = await api(state.editing ? `/conferences/${state.editing}` : "/conferences", state.editing ? "PUT" : "POST", config);
    state.selected = result.id; state.page = 1; $("#conference-dialog").close(); setView("library");
    await refresh(); notice("Conference saved. Click Scrape papers to collect its index and content.");
  } catch (error) { $("#conference-form-error").textContent = error.message; }
});
$("#key-form").addEventListener("submit", safely(async (event) => {
  event.preventDefault(); await api("/key", "PUT", {api_key: $("#api-key").value}); $("#api-key").value = "";
  await refresh(); $("#connection-state").textContent = "Key configured for this session. It will be verified on the first tagging request."; notice("TypeSafe key configured for this server session.");
}));
$("#add-domain").addEventListener("click", () => { addDomainEditor(); resetPromptTestOutput(); });
for (const event of ["input", "change"]) $("#tagging-form").addEventListener(event, resetPromptTestOutput);
$("#tagging-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#tagging-form-error").textContent = "";
  if (!validateTaggingForm()) return;
  try {
    await api("/settings", "PUT", draftTaggingConfig());
    await refresh(); notice("Tagging settings saved. Estimates and matching tags have been recalculated.");
  } catch (error) { $("#tagging-form-error").textContent = error.message; }
});
$("#preview-prompt-test").addEventListener("click", async () => {
  resetPromptTestOutput();
  if (!validateTaggingForm()) return;
  try {
    const request = {conference_id:Number($("#prompt-test-conference").value),limit:promptTestLimit(),tagging:draftTaggingConfig()};
    const preview = await api("/prompt-test/estimate", "POST", request);
    const conference = state.conferences.find((item) => item.id === request.conference_id);
    state.pendingPromptTest = {request:{...request,estimate_signature:preview.estimate_signature},preview};
    $("#prompt-test-confirmation").textContent = `${number(preview.paper_count)} complete papers from ${conference.config.name} ${conference.config.year} will be sent to ${request.tagging.model}. Estimated input: ${number(preview.input_tokens)} tokens; estimated cost: ${money(preview.estimated_cost)} (${money(preview.low_cost)}–${money(preview.high_cost)} sensitivity range).${state.settings.api_key_configured ? "" : " Connect a TypeSafe API key before running the test."}`;
    $("#confirm-prompt-test").disabled = !state.settings.api_key_configured;
    $("#prompt-test-dialog").showModal();
  } catch (error) { $("#prompt-test-error").textContent = error.message; }
});
$("#confirm-prompt-test").addEventListener("click", async () => {
  const pending = state.pendingPromptTest;
  if (!pending) return;
  const button = $("#confirm-prompt-test");
  button.disabled = true; button.textContent = "Running…";
  try {
    const result = await api("/prompt-test", "POST", pending.request);
    $("#prompt-test-dialog").close();
    renderPromptTestResults(result, pending.request.tagging);
    state.pendingPromptTest = null;
  } catch (error) {
    $("#prompt-test-dialog").close();
    $("#prompt-test-error").textContent = error.message;
  } finally {
    button.textContent = "Run test";
    button.disabled = !state.settings.api_key_configured;
  }
});
$("#field-selection-options").addEventListener("change", safely(saveFieldSelection));
$("#field-selection").addEventListener("click", () => {
  if (!$("#field-selection-panel").hidden) { closeFieldSelection(); return; }
  closeTagSelection(); closeDomainFilter();
  $("#field-selection-panel").hidden = false;
  $("#field-selection").setAttribute("aria-expanded", "true");
});
$("#field-selection-picker").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); closeFieldSelection(true); }
});
$("#scrape-button").addEventListener("click", safely(() => startJob("scrape")));
$("#tag-button").addEventListener("click", safely(() => openTagDialog(false)));
$("#tag-domain-options").addEventListener("change", safely(updateTagSelectionEstimate));
$("#tag-selection").addEventListener("click", () => {
  if (!$("#tag-selection-panel").hidden) { closeTagSelection(); return; }
  closeDomainFilter();
  $("#tag-selection-search").value = "";
  filterTagOptions();
  $("#tag-selection-panel").hidden = false;
  $("#tag-selection").setAttribute("aria-expanded", "true");
  $("#tag-selection-search").focus();
});
$("#tag-selection-search").addEventListener("input", filterTagOptions);
$("#tag-selection-picker").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); closeTagSelection(true); }
});
$("#clear-tag-selection").addEventListener("click", safely(async () => {
  $$("input[name=tag-domain]").forEach((input) => input.checked = false);
  await updateTagSelectionEstimate();
}));
$("#confirm-tag").addEventListener("click", safely(async () => {
  const reviewed = state.confirmedTag;
  if (!reviewed) throw new Error("Review the tagging request before starting.");
  await startJob("tag", reviewed);
  state.tagSelectionKey = null; state.tagSelectionStats = null; state.pendingTag = null; state.confirmedTag = null;
  $("#tag-dialog").close();
  await refresh();
}));
document.addEventListener("submit", safely(async (event) => {
  if (event.target.id !== "arxiv-form") return;
  event.preventDefault(); await api(`/papers/${event.target.dataset.id}/arxiv`, "PUT", {arxiv_id:$("#paper-arxiv-id").value.trim()});
  $("#paper-dialog").close(); await refresh(); notice("arXiv ID saved. Run Scrape papers to verify and retrieve its introduction.");
}));
$("#domain-filter").addEventListener("click", () => {
  if (!$("#domain-filter-panel").hidden) { closeDomainFilter(); return; }
  $("#domain-filter-search").value = "";
  filterDomainOptions();
  $("#domain-filter-panel").hidden = false;
  $("#domain-filter").setAttribute("aria-expanded", "true");
  $("#domain-filter-search").focus();
});
$("#domain-filter-search").addEventListener("input", filterDomainOptions);
$("#domain-filter-options").addEventListener("change", safely(async (event) => {
  if (event.target.name !== "filter-domain") return;
  const input = event.target;
  state.filterDomainIds = state.filterDomainIds.filter((id) => id !== input.value);
  if (input.checked) state.filterDomainIds.push(input.value);
  renderDomainFilter();
  state.page = 1;
  await loadPapers();
}));
document.addEventListener("click", (event) => {
  if (!$("#domain-picker").contains(event.target)) closeDomainFilter();
  if (!$("#tag-selection-picker").contains(event.target)) closeTagSelection();
  if (!$("#field-selection-picker").contains(event.target)) closeFieldSelection();
});
document.addEventListener("focusin", (event) => {
  if (!$("#domain-picker").contains(event.target)) closeDomainFilter();
  if (!$("#tag-selection-picker").contains(event.target)) closeTagSelection();
  if (!$("#field-selection-picker").contains(event.target)) closeFieldSelection();
});
$("#domain-picker").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); closeDomainFilter(true); }
});
$("#clear-domain-filter").addEventListener("click", safely(async () => {
  state.filterDomainIds = [];
  renderDomainFilter();
  state.page = 1;
  await loadPapers();
}));
let searchTimer;
$("#paper-search").addEventListener("input", () => { state.zoteroList = null; updateZoteroContext(); clearTimeout(searchTimer); searchTimer = setTimeout(safely(async () => {state.page = 1; await loadPapers();}), 250); });
for (const selector of ["#status-filter"]) $(selector).addEventListener("change", safely(async () => {state.page = 1; await loadPapers();}));
$("#previous-page").addEventListener("click", safely(async () => {state.page--; await loadPapers();}));
$("#next-page").addEventListener("click", safely(async () => {state.page++; await loadPapers();}));

// Feature-detected WebMCP uses the same API and refresh path as the interface.
if (document.modelContext?.registerTool) {
  const lifecycle = new AbortController();
  const tools = [
    {name:"get_conference_statistics",description:"Read saved paper coverage and estimated tagging costs for every conference.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:async (input) => {
      if (!input || Object.keys(input).length) throw new Error("This tool accepts an empty object.");
      const data = await api("/conferences"); return data.conferences.map((conf) => ({id:conf.id,name:conf.config.name,year:conf.config.year,stats:conf.stats}));
    }},
    {name:"add_conference",description:"Save a new ICML, ECCV, ICLR, CVPR, ICCV, or NeurIPS edition in the workspace. Does not scrape or send paid model requests.",inputSchema:{type:"object",properties:{name:{type:"string",enum:["ICML","ECCV","ICLR","CVPR","ICCV","NeurIPS"]},year:{type:"integer",minimum:2000,maximum:2100}},required:["name","year"],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},execute:async (input) => {
      if (!input || !["ICML","ECCV","ICLR","CVPR","ICCV","NeurIPS"].includes(input.name) || !Number.isInteger(input.year) || Object.keys(input).sort().join(",") !== "name,year") throw new Error("Provide a supported name and integer year.");
      const result = await api("/conferences","POST",{name:input.name,year:input.year}); state.selected = result.id; setView("library"); await refresh(); return result;
    }}
  ];
  for (const tool of tools) {
    try { Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch((error) => console.warn("WebMCP unavailable:",error.message)); }
    catch (error) { console.warn("WebMCP unavailable:",error.message); }
  }
  window.addEventListener("pagehide", () => lifecycle.abort(), {once:true});
}
addFieldHelp();
refresh();
setInterval(() => { if (state.view === "library" && !document.hidden) refresh(); }, 4000);
