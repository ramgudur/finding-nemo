// Wraps every route except EndUser's (/apps/:appId/go keeps its own full-bleed public chrome).
// The working pages (builder, review, keys, console) carry .wp, which switches the colour tokens to the
// working palette (src/styles/tokens.css); Home and Past Projects keep the original values.
import { Outlet, useLocation } from "react-router-dom";
import { Header } from "./Header";

export function MainLayout() {
  const { pathname } = useLocation();
  const working = pathname.startsWith("/new") || pathname.startsWith("/apps/");
  return (
    <div className={`min-h-screen bg-mist ${working ? "wp" : ""}`}>
      <Header />
      <Outlet />
    </div>
  );
}
