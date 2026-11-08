// Wraps every route except EndUser's (/apps/:appId/go keeps its own full-bleed public chrome).
import { Outlet } from "react-router-dom";
import { Header } from "./Header";

export function MainLayout() {
  return (
    <div className="min-h-screen bg-mist">
      <Header />
      <Outlet />
    </div>
  );
}
