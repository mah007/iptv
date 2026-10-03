import { QRCodeSVG } from "qrcode.react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export interface QRCodeCardProps {
  value: string;
  /** What the code contains, for screen readers (e.g. "QR code with the login details"). */
  label: string;
  title?: ReactNode;
  description?: ReactNode;
  /** Rendered size in CSS pixels. */
  size?: number;
  footer?: ReactNode;
  className?: string;
}

/**
 * QR code on a white tile in both themes: scanners need dark modules on a
 * light quiet zone.
 */
export function QRCodeCard({
  value,
  label,
  title,
  description,
  size = 176,
  footer,
  className,
}: QRCodeCardProps) {
  return (
    <figure
      data-slot="qr-code-card"
      className={cn(
        "flex flex-col items-center gap-3 rounded-card border border-border bg-card p-(--density-card) text-center shadow-xs",
        className,
      )}
    >
      {title || description ? (
        <figcaption className="grid gap-1">
          {title ? <span className="text-sm font-semibold text-foreground">{title}</span> : null}
          {description ? (
            <span className="text-ui text-muted-foreground">{description}</span>
          ) : null}
        </figcaption>
      ) : null}
      <div className="rounded-input bg-white p-3 shadow-xs ring-1 ring-black/5">
        <QRCodeSVG
          value={value}
          size={size}
          level="M"
          marginSize={0}
          bgColor="#ffffff"
          fgColor="#09090b"
          role="img"
          aria-label={label}
        />
      </div>
      {footer}
    </figure>
  );
}
