import { useCallback, useEffect, useState } from "react";

function readView() {
  const hash = (window.location.hash || "#/").replace(/^#/, "");
  if (hash.startsWith("/workspace")) return "workspace";
  return "landing";
}

export function useAppRoute() {
  const [view, setView] = useState(readView);

  useEffect(() => {
    const onHash = () => setView(readView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const enterWorkspace = useCallback(() => {
    if (window.location.hash !== "#/workspace") {
      window.location.hash = "#/workspace";
    }
    setView("workspace");
  }, []);

  const goHome = useCallback(() => {
    if (window.location.hash !== "#/" && window.location.hash !== "") {
      window.location.hash = "#/";
    }
    setView("landing");
  }, []);

  return { view, enterWorkspace, goHome };
}
