/** svkit mark: a clock waveform in a rounded tile. Colours come from theme tokens. */
export function LogoMark({ size = 20 }: { size?: number }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <rect x="0.5" y="0.5" width="19" height="19" rx="4.5" fill="var(--accent)" />
      <path d="M3.5 13.5h3v-7h4v7h3v-7h3" fill="none" stroke="var(--accent-fg)" strokeWidth="1.8"
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/** Top-left brand: mark + wordmark, links to the Runs view. */
export function Logo() {
  return (
    <a className="brand" href="#/" aria-label="svkit home">
      <LogoMark />
      <span className="brand-word">svkit</span>
    </a>
  );
}
