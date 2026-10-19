// The bootstrap, and the only classic script here.
//
// Injected on every click of the toolbar icon, so its first job is to notice it
// has run before: a second click closes the panel rather than stacking another
// one on top.
//
// Which panel opens depends on the site, because the panel drives the DOM of the
// very tab it runs in and cannot reach across to another site. On an AI site it
// is the resume panel, tied to that site's engine — chatgpt.com drives ChatGPT,
// claude.ai drives Claude — and nowhere else; everywhere else it is the job
// clipper. The panels never share a page, so neither has to make room for the
// other. To write resumes with the other AI, open the panel in a tab on the
// other site.
//
// Everything past this point is a module, reached by dynamic import of an
// extension URL. A content script cannot be declared as a module, but it may
// import one, and that keeps the panel's code sharing lib/ with the rest of the
// extension instead of being a flattened copy of it.

// The AI sites the resume panel runs on, each with the engine the page steps
// run under and its own host element id. Order does not matter; the first whose
// pattern matches the tab wins.
const AI_SITES = [
  { engine: "gpt", host: /(^|\.)chatgpt\.com$/, hostId: "poong-gpt-host" },
  { engine: "claude", host: /(^|\.)claude\.ai$/, hostId: "poong-claude-host" },
];

(async () => {
  const site = AI_SITES.find((s) => s.host.test(location.hostname));
  const HOST_ID = site ? site.hostId : "poong-clipper-host";
  const MODULE = site ? "lib/engine-panel.js" : "lib/panel.js";

  const open = document.getElementById(HOST_ID);
  if (open) {
    // Closing through the panel's own handler, so its re-attach watcher knows
    // this removal was meant and does not undo it.
    if (typeof open.__poongClose === "function") open.__poongClose();
    else open.remove();
    return;
  }

  try {
    const { mountPanel } = await import(chrome.runtime.getURL(MODULE));
    await mountPanel(HOST_ID, site ? site.engine : undefined);
  } catch (err) {
    console.error("[Poong] could not open the panel:", err);
  }
})();
