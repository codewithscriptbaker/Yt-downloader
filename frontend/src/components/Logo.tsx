"use client";

import { useId } from "react";

type LogoProps = {
  className?: string;
  /** Pixel size of the mark (square). Default 28. */
  size?: number;
  title?: string;
};

/** MediaPort brand mark — teal tile with download-into-port glyph. */
export function Logo({ className, size = 28, title = "MediaPort" }: LogoProps) {
  const uid = useId().replace(/:/g, "");
  const gradId = `mp-g-${uid}`;

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={title}
    >
      <defs>
        <linearGradient
          id={gradId}
          x1="4"
          y1="2"
          x2="28"
          y2="30"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#00a88a" />
          <stop offset="1" stopColor="#006b58" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${gradId})`} />
      {/* Stem */}
      <rect x="14.5" y="6.8" width="3" height="10" rx="1.5" fill="#fff" />
      {/* Arrow head */}
      <path
        d="M16 22.2 9.8 15.6a1.45 1.45 0 0 1 2.1-2L16 18l4.1-4.4a1.45 1.45 0 1 1 2.1 2L16 22.2Z"
        fill="#fff"
      />
      {/* Port tray */}
      <path
        d="M8.5 23.4h15c.83 0 1.5.67 1.5 1.5v.4c0 .83-.67 1.5-1.5 1.5h-15c-.83 0-1.5-.67-1.5-1.5v-.4c0-.83.67-1.5 1.5-1.5Z"
        fill="#fff"
      />
    </svg>
  );
}

/** Wordmark + mark for headers. */
export function BrandLockup({ size = 28 }: { size?: number }) {
  return (
    <span className="brand-lockup">
      <Logo size={size} />
      <span className="brand-lockup__name">MediaPort</span>
    </span>
  );
}
