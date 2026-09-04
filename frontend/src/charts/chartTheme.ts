// Shared Highcharts theming, derived from the same CSS custom properties
// theme/variables.css defines (never a hex literal duplicated here) so brand
// updates in one place propagate to both the plain UI and the charts.
import type { Options } from "highcharts";

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function chartColors() {
  return {
    primary: cssVar("--color-primary", "#38af48"),
    primaryHover: cssVar("--color-primary-hover", "#2a8f38"),
    secondary: cssVar("--color-secondary", "#8ed1fc"),
    secondarySoft: cssVar("--color-secondary-soft", "#eaf7fe"),
    text: cssVar("--color-text", "#333333"),
    heading: cssVar("--color-heading", "#32373c"),
    textMuted: cssVar("--color-text-muted", "#5b5f63"),
    borderSoft: cssVar("--color-border-soft", "#d8dbdd"),
    good: cssVar("--color-good", "#2a8f38"),
    warning: cssVar("--color-warning", "#a8710b"),
    critical: cssVar("--color-critical", "#b3261e"),
    neutral: cssVar("--color-neutral", "#32373c"),
    surface: cssVar("--color-surface", "#ffffff"),
    fontSans: cssVar("--font-sans", "-apple-system, sans-serif"),
  };
}

// Brand primary/secondary first, then warning/critical/neutral so a 3+
// series chart (delay reasons, vendor scorecard) still reads as intentional
// rather than looping back to green.
export function seriesPalette(): string[] {
  const c = chartColors();
  return [c.primary, c.secondary, c.warning, c.critical, c.textMuted, c.primaryHover];
}

export function baseChartOptions(): Options {
  const c = chartColors();
  return {
    chart: {
      backgroundColor: "transparent",
      style: { fontFamily: c.fontSans },
      spacing: [8, 8, 8, 8],
    },
    title: { text: undefined },
    credits: { enabled: false },
    colors: seriesPalette(),
    legend: {
      itemStyle: { color: c.text, fontSize: "12px", fontWeight: "500" },
      itemHoverStyle: { color: c.heading },
    },
    xAxis: {
      lineColor: c.borderSoft,
      tickColor: c.borderSoft,
      labels: { style: { color: c.textMuted, fontSize: "11px" } },
    },
    yAxis: {
      gridLineColor: c.borderSoft,
      title: { text: undefined },
      labels: { style: { color: c.textMuted, fontSize: "11px" } },
    },
    tooltip: {
      backgroundColor: c.surface,
      borderColor: c.borderSoft,
      style: { color: c.text, fontSize: "12px" },
    },
  };
}
