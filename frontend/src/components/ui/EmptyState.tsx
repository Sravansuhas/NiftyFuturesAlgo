import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  message?: string;
  action?: ReactNode;
  /** centered = modal-style; inline = left-aligned in a tile (default). */
  variant?: 'inline' | 'centered';
}

export default function EmptyState({
  icon: Icon,
  title,
  message,
  action,
  variant = 'inline',
}: EmptyStateProps) {
  return (
    <div className={`empty-state empty-state--${variant}`}>
      {Icon && <Icon size={variant === 'centered' ? 28 : 22} className="text-muted opacity-80" />}
      <div className="empty-state-body">
        <p className="text-main font-semibold m-0">{title}</p>
        {message && <p className="text-sm text-muted m-0 leading-relaxed">{message}</p>}
        {action}
      </div>
    </div>
  );
}