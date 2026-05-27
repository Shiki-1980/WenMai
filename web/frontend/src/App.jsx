import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Write from "./pages/Write";
import Entities from "./pages/Entities";
import Chapters from "./pages/Chapters";
import Audit from "./pages/Audit";
import Novels from "./pages/Novels";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/write" element={<Write />} />
        <Route path="/entities" element={<Entities />} />
        <Route path="/chapters" element={<Chapters />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/novels" element={<Novels />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
