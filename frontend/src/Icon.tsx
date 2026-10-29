export type IconName =
  | "bread"
  | "plan"
  | "data"
  | "model"
  | "history"
  | "arrow"
  | "check"
  | "alert"
  | "info"
  | "download";

export function Icon({
  name,
  className = "",
}: {
  name: IconName;
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
    check: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="m8 12.5 2.7 2.7L16.5 9" />
      </>
    ),
    alert: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7.5v5.5m0 3.5v.01" />
      </>
    ),
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6m0-9.5v.01" />
      </>
    ),
    download: (
      <>
        <path d="M12 4v11m-5-5 5 5 5-5M5 20h14" />
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

/** BakePlan monogram, the same mark as in the pitch deck and README. */
export function BrandMark() {
  return (
    <svg
      className="brand-logo"
      viewBox="0 0 160 160"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="2" y="2" width="156" height="156" rx="34" fill="#F7F2EC" />
      <g transform="translate(37 28) scale(1.45)">
        <path
          fill="#1A1614"
          d="M0 0h14v72H0zM14 40h26c24 0 24 32 0 32H14V59h24c7 0 7-6 0-6H14z"
        />
        <path
          fill="#C2341F"
          fillRule="evenodd"
          d="M20 0h18c25 0 25 34 0 34H20zM20 12v10h16c8 0 8-10 0-10z"
        />
      </g>
    </svg>
  );
}
