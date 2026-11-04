// Sidebar icons: 16px line drawings in the current text colour (original, no icon library).
const P: Record<string, JSX.Element> = {
  runs: (
    <>
      <circle cx="3" cy="4" r="1" fill="currentColor" stroke="none" />
      <circle cx="3" cy="8" r="1" fill="currentColor" stroke="none" />
      <circle cx="3" cy="12" r="1" fill="currentColor" stroke="none" />
      <path d="M6 4h8M6 8h8M6 12h8" />
    </>
  ),
  tests: (
    <>
      <rect x="2" y="2" width="12" height="12" rx="2.5" />
      <path d="M5 8.2l2.1 2.1L11 6" />
    </>
  ),
  waves: <path d="M1 11h2.5V5H7v6h2.5V5H13v6h2" />,
  design: (
    <>
      <rect x="1.5" y="2.5" width="5" height="4" rx="1" />
      <rect x="9.5" y="9.5" width="5" height="4" rx="1" />
      <path d="M6.5 4.5H8v7h1.5" />
    </>
  ),
  xtrace: (
    <>
      <circle cx="3.5" cy="8" r="1.8" />
      <path d="M5.3 8H8M8 4v8M8 4h2.5M8 12h2.5" />
      <path d="M11.5 2.5l3 3M14.5 2.5l-3 3" />
      <circle cx="13" cy="12" r="1.5" />
    </>
  ),
  synth: (
    <>
      <rect x="4" y="4" width="8" height="8" rx="1" />
      <rect x="6.5" y="6.5" width="3" height="3" />
      <path d="M6 1.5V4M10 1.5V4M6 12v2.5M10 12v2.5M1.5 6H4M1.5 10H4M12 6h2.5M12 10h2.5" />
    </>
  ),
  sweep: (
    <>
      <path d="M1.5 14.5h13M1.5 14.5v-13" />
      <path d="M3 4c2 0 2.5 7 5 7s3-3.5 5.5-3.5" />
      <path d="M8 13.2V11" strokeDasharray="1 1.2" />
    </>
  ),
  perf: (
    <>
      <path d="M2.2 12.5a6 6 0 1 1 11.6 0" />
      <path d="M8 11l3-4.5" />
      <circle cx="8" cy="11" r="1" fill="currentColor" stroke="none" />
    </>
  ),
};

export function NavIcon({ name }: { name: string }) {
  return (
    <svg className="nav-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {P[name] ?? <circle cx="8" cy="8" r="5" />}
    </svg>
  );
}
