import type { ChartSeriesData, PieChartData } from "../api";

// Structural emptiness (no series / no points at all) -- not "every value
// happens to be zero", which is a legitimate reading (e.g. 0 incidents this
// week) and should still render a chart, just a flat one.
export function isChartSeriesEmpty(data: ChartSeriesData): boolean {
  return (
    data.categories.length === 0 ||
    data.series.length === 0 ||
    data.series.every((s) => s.data.length === 0)
  );
}

export function isPieEmpty(data: PieChartData): boolean {
  return data.series.length === 0 || data.series.every((s) => s.data.length === 0);
}
