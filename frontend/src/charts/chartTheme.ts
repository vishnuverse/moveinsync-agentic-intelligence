// Shared Highcharts theming, derived from the same CSS custom properties
// theme/variables.css defines (never a hex literal duplicated here) so brand
// updates in one place propagate to both the plain UI and the charts.
import type { Options } from "highcharts";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// BUGFIX (found live: rotation alone didn't fix the OTA Trend / No-Show Rate
// x-axis -- a 30-day view still renders ~10 category labels after
// Highcharts' own thinning, and a full "2026-07-02" string rotated -45 is
// still wider than the ~23px tick spacing in a card-sized chart, so labels
// kept visually colliding even though each one WAS correctly rotated).
// Shortening only genuine ISO-date categories to "Jul 2" (leaving
// non-date categories like "NODELAY"/"TRAFFIC" untouched) is what actually
// makes them fit; kept here, not per-chart, for the same one-rule-for-every-
// chart reason autoRotation above is shared.
function formatAxisLabel(this: { value: string | number }): string {
  const raw = String(this.value);
  if (!ISO_DATE_RE.test(raw)) return raw;
  const d = new Date(`${raw}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

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
      // One shared rule for every chart wrapper instead of each picking its
      // own label-thinning strategy (found live: TrendLineChart's fixed
      // `step` skip left OTA Trend/No-Show Rate labels overlapping into
      // smashed text at higher category counts, while StackedAreaChart's
      // reliance on Highcharts' own auto-rotation held up fine for the same
      // kind of daily-category data).
      labels: {
        style: { color: c.textMuted, fontSize: "11px" },
        autoRotation: [-20, -45],
        autoRotationLimit: 60,
        formatter: formatAxisLabel,
      },
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
