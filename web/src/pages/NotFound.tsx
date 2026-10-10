import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="py-24 text-center">
      <p className="text-lg font-medium">Page not found</p>
      <Link to="/" className="mt-3 inline-block text-sm text-accent-600 hover:underline dark:text-accent-400">
        Back to agents
      </Link>
    </div>
  );
}
