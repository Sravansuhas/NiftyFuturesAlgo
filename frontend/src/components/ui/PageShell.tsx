import type { ReactNode } from 'react';

interface PageShellProps {
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Full width (default) or cap readable line length — always left-aligned. */
  maxWidth?: number | 'full';
  className?: string;
}

/** Consistent page wrapper — title lives in Layout header; pages use subtitle only. */
export default function PageShell({
  subtitle,
  actions,
  children,
  maxWidth = 'full',
  className = '',
}: PageShellProps) {
  const widthClass = maxWidth === 'full' ? '' : 'page-shell--readable';

  return (
    <div
      className={`page-shell ${widthClass} ${className}`.trim()}
      style={maxWidth === 'full' ? undefined : { maxWidth, width: '100%' }}
    >
      {(subtitle || actions) && (
        <div className="page-shell-bar">
          {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
          {actions ? <div className="header-actions">{actions}</div> : null}
        </div>
      )}
      {children}
    </div>
  );
}