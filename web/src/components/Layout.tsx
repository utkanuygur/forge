import { Link, NavLink, Outlet } from "react-router-dom";
import { Settings } from "lucide-react";

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? "font-medium text-neutral-900 dark:text-neutral-100" : "hover:text-neutral-900 dark:hover:text-neutral-100";
import Logo from "./Logo";

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-neutral-200 bg-white/80 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4 sm:px-6">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <Logo /> Forge
          </Link>
          <nav className="flex flex-1 gap-4 text-sm text-neutral-600 dark:text-neutral-400">
            <NavLink to="/" end className={navClass}>
              Agents
            </NavLink>
            <NavLink to="/dashboard" className={navClass}>
              Dashboard
            </NavLink>
            <NavLink to="/settings" className={(s) => `${navClass(s)} ml-auto inline-flex items-center gap-1.5`} aria-label="Settings">
              <Settings className="h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">Settings</span>
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}
