import { useTheme } from "./hooks/useTheme.js";
import { useAppRoute } from "./hooks/useAppRoute.js";
import LandingPage from "./components/LandingPage.jsx";
import WorkspaceApp from "./WorkspaceApp.jsx";

export default function App() {
  const { theme, toggleTheme } = useTheme();
  const { view, enterWorkspace, goHome } = useAppRoute();

  if (view === "workspace") {
    return (
      <WorkspaceApp onGoHome={goHome} theme={theme} onToggleTheme={toggleTheme} />
    );
  }

  return (
    <LandingPage onEnter={enterWorkspace} theme={theme} onToggleTheme={toggleTheme} />
  );
}
