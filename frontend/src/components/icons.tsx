// Small inline icon set, replacing raw emoji glyphs (⏱ ⚡ 🔍 ✕ ▲▼▬) that
// carried no aria-label and render inconsistently across platforms/fonts.
// All icons inherit color via currentColor and are purely decorative
// (aria-hidden) -- callers own the accessible name via surrounding text or
// an explicit aria-label on the interactive element.
import type { SVGProps } from "react";

function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function IconClock(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </Icon>
  );
}

export function IconBolt(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" />
    </Icon>
  );
}

export function IconSearch(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.2-3.2" />
    </Icon>
  );
}

export function IconClose(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="m5 5 14 14M19 5 5 19" />
    </Icon>
  );
}

export function IconTrendUp(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 16 10 10 14 14 20 6" />
      <path d="M14 6h6v6" />
    </Icon>
  );
}

export function IconTrendDown(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 8 10 14 14 10 20 18" />
      <path d="M14 18h6v-6" />
    </Icon>
  );
}

export function IconTrendFlat(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 12h16" />
      <path d="m17 8 3 4-3 4" />
    </Icon>
  );
}

export function IconAlert(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M12 3.5 1.5 21h21L12 3.5Z" strokeLinejoin="round" />
      <path d="M12 9.5v4.5" />
      <circle cx="12" cy="17" r="0.5" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function IconRoute(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <path d="M6 8.5v3a3 3 0 0 0 3 3h3a3 3 0 0 1 3 3v0.5" />
    </Icon>
  );
}

export function IconPlus(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function IconTrash(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 7h16" />
      <path d="M9 7V4h6v3" />
      <path d="M6 7l1 13h10l1-13" />
    </Icon>
  );
}

export function IconPencil(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3Z" strokeLinejoin="round" />
    </Icon>
  );
}
