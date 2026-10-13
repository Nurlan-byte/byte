export function Icon({
  name,
  className = "",
}: {
  name: "bread" | "plan" | "data" | "model" | "history" | "arrow";
  className?: string;
}) {
  const paths = {
    bread: (
      <>
        <path d="M5 9a4 4 0 0 1 1-7h12a4 4 0 0 1 1 7v11H5Z" />
        <path d="m9 6 2 3m3-3 2 3" />
      </>
    ),
    plan: (
      <>
        <rect x="4" y="4" width="16" height="17" rx="2" />
        <path d="M8 2v4m8-4v4M4 10h16m-12 4h3m-3 3h7" />
      </>
    ),
    data: (
      <>
        <path d="M5 3h10l4 4v14H5Z" />
        <path d="M14 3v5h5M8 12h8m-8 4h8" />
      </>
    ),
    model: (
      <>
        <path d="M4 20V4m0 16h16M8 15l4-5 4 2 4-7" />
        <circle cx="12" cy="10" r="1" />
      </>
    ),
    history: (
      <>
        <path d="M3 10a9 9 0 1 1 2 8M3 4v6h6m3-3v6l4 2" />
      </>
    ),
    arrow: (
      <>
        <path d="M4 12h16m-6-6 6 6-6 6" />
      </>
    ),
  };
  return (
    <svg
      className={`icon ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
