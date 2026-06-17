import type { ReactNode } from 'react';

interface PageShellProps {
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Full width (default) or cap readable line length — always left-aligned. */
  maxWidth?: number | 'full';
  className?: string;
  /** Extra class on the subtitle/actions bar (e.g. page-shell-bar--toolbar). */
  barClassName?: string;
}

/** Consistent page wrapper — title lives in Layout header; pages use subtitle only. */
export default function PageShell({
  subtitle,
  actions,
  children,
  maxWidth = 'full',
  className = '',
  barClassName = '',
}: PageShellProps) {
  const widthClass = maxWidth === 'full' ? '' : 'page-shell--readable';

  return (
    <div
      className={`page-shell ${widthClass} ${className}`.trim()}
      style={maxWidth === 'full' ? undefined : { maxWidth, width: '100%' }}
    >
      {(subtitle || actions) && (
        <div className={`page-shell-bar ${barClassName}`.trim()}>
          {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
          {actions ? <div className="page-shell-toolbar header-actions">{actions}</div> : null}
        </div>
      )}
      {children}
    </div>
  );
}