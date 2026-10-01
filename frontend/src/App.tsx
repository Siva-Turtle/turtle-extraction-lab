import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import Agents from "./pages/Agents";
import Attributes from "./pages/Attributes";
import Logs from "./pages/Logs";
import TestLab from "./pages/TestLab";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<TestLab />} />
        <Route path="/test" element={<TestLab />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/attributes" element={<Attributes />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
