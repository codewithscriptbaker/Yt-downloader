import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

/** Apple touch icon — same MediaPort mark as favicon / UI logo. */
export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "linear-gradient(145deg, #00a88a 0%, #006b58 100%)",
        }}
      >
        <svg width="112" height="112" viewBox="0 0 32 32" fill="none">
          <rect x="14.5" y="6.8" width="3" height="10" rx="1.5" fill="#fff" />
          <path
            d="M16 22.2 9.8 15.6a1.45 1.45 0 0 1 2.1-2L16 18l4.1-4.4a1.45 1.45 0 1 1 2.1 2L16 22.2Z"
            fill="#fff"
          />
          <path
            d="M8.5 23.4h15c.83 0 1.5.67 1.5 1.5v.4c0 .83-.67 1.5-1.5 1.5h-15c-.83 0-1.5-.67-1.5-1.5v-.4c0-.83.67-1.5 1.5-1.5Z"
            fill="#fff"
          />
        </svg>
      </div>
    ),
    { ...size },
  );
}
