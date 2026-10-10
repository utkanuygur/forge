export default function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" className="fill-accent-600" />
      <path d="M10 9h12v3.5h-8V15h6.5v3.5H14V23h-4z" fill="#fff" />
    </svg>
  );
}
