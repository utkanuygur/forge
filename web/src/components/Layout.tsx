import { Link, Outlet } from "react-router-dom";
import Logo from "./Logo";

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-neutral-200 bg-white/80 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4 sm:px-6">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <Logo /> Forge
          </Link>
          <nav className="text-sm text-neutral-600 dark:text-neutral-400">
            <Link to="/" className="hover:text-neutral-900 dark:hover:text-neutral-100">
              Agents
            </Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}
