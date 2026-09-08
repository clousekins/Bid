// The bootstrap, and the only classic script here.
//
// Injected on every click of the toolbar icon, so its first job is to notice it
// has run before: a second click closes the panel rather than stacking another
// one on top.
//
// Everything past this point is a module, reached by dynamic import of an
// extension URL. A content script cannot be declared as a module, but it may
// import one, and that keeps the panel's code sharing lib/ with the rest of the
// extension instead of being a flattened copy of it.

(async () => {
  const HOST_ID = "poong-clipper-host";

  const open = document.getElementById(HOST_ID);
  if (open) {
    open.remove();
    return;
  }

  try {
    const { mountPanel } = await import(chrome.runtime.getURL("lib/panel.js"));
    await mountPanel(HOST_ID);
  } catch (err) {
    console.error("[Poong Job Clipper] could not open the panel:", err);
  }
})();
