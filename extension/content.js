// The bootstrap, and the only classic script here.
//
// Injected on every click of the toolbar icon, so its first job is to notice it
// has run before: a second click closes the panel rather than stacking another
// one on top.
//
// Which panel opens depends on the site. On chatgpt.com it is the ChatGPT
// resume panel and nothing else; everywhere else it is the job clipper. The
// two never share a page, so neither has to make room for the other.
//
// Everything past this point is a module, reached by dynamic import of an
// extension URL. A content script cannot be declared as a module, but it may
// import one, and that keeps the panel's code sharing lib/ with the rest of the
// extension instead of being a flattened copy of it.

(async () => {
  const onChatGPT = /(^|\.)chatgpt\.com$/.test(location.hostname);
  const HOST_ID = onChatGPT ? "poong-gpt-host" : "poong-clipper-host";
  const MODULE = onChatGPT ? "lib/gpt-panel.js" : "lib/panel.js";

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
    await mountPanel(HOST_ID);
  } catch (err) {
    console.error("[Poong] could not open the panel:", err);
  }
})();
