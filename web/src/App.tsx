import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AgentsPage from "./pages/AgentsPage";
import AgentPage from "./pages/AgentPage";
import RunPage from "./pages/RunPage";
import SharePage from "./pages/SharePage";
import NotFound from "./pages/NotFound";
import DashboardPage from "./pages/DashboardPage";
import SettingsPage from "./pages/SettingsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/s/:slug" element={<SharePage />} />
      <Route element={<Layout />}>
        <Route path="/" element={<AgentsPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/agents/:slug" element={<AgentPage />} />
        <Route path="/agents/:slug/:tab" element={<AgentPage />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
