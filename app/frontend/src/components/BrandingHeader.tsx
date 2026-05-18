export function BrandingHeader() {
  return (
    <div className="flex flex-col items-center mb-2">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-medium tracking-tight text-[var(--text-primary)]">
          FirstSpirit
        </span>
        <span className="brand-serif text-2xl text-[var(--accent)]">Docs</span>
      </div>
      <span className="mt-1 text-sm text-[var(--text-secondary)]">
        Ask the FirstSpirit &amp; Crownpeak documentation anything
      </span>
    </div>
  );
}
