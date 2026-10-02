import { Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { ModulesView } from "./app/ModulesView";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/*" element={<ModulesView />} />
      </Route>
    </Routes>
  );
}
