import Highcharts from "highcharts";
import type { ChartSeriesData } from "../api";
import { baseChartOptions, chartColors } from "./chartTheme";
import HighchartsReact from "./HighchartsReactCompat";
import "./charts.css";

interface TrendLineChartProps {
  data: ChartSeriesData;
  valueSuffix?: string;
  /** Sparkline mode: no axes/legend/tooltip chrome, for embedding in a table row (vendor scorecard). */
  compact?: boolean;
  height?: number;
}

export function TrendLineChart({ data, valueSuffix = "", compact = false, height }: TrendLineChartProps) {
  const c = chartColors();
  const hasBenchmark = !compact && (data.target != null || data.breach_threshold != null);
  const plotLines: Highcharts.YAxisPlotLinesOptions[] = [];
  const plotBands: Highcharts.YAxisPlotBandsOptions[] = [];
  if (hasBenchmark && data.target != null) {
    plotLines.push({
      value: data.target,
      color: c.textMuted,
      dashStyle: "Dash",
      width: 1.5,
      zIndex: 4,
      label: { text: data.target_label ?? `Target ${data.target}${valueSuffix}`, style: { color: c.textMuted, fontSize: "10px" } },
    });
  }
  if (hasBenchmark && data.breach_threshold != null) {
    const axisValues = data.series.flatMap((s) => s.data);
    const axisMin = Math.min(data.breach_threshold, ...axisValues, 0);
    plotBands.push({
      from: axisMin,
      to: data.breach_threshold,
      color: "rgba(179, 38, 30, 0.08)",
      zIndex: 2,
    });
  }
  const options: Highcharts.Options = Highcharts.merge(baseChartOptions(), {
    chart: { type: "line", height: height ?? (compact ? 32 : 260) },
    xAxis: {
      categories: data.categories,
      visible: !compact,
      labels: { step: Math.max(1, Math.floor(data.categories.length / 8)) },
    },
    yAxis: {
      visible: !compact,
      labels: { format: `{value}${valueSuffix}` },
      plotLines,
      plotBands,
    },
    legend: { enabled: !compact && data.series.length > 1 },
    tooltip: {
      enabled: !compact,
      valueSuffix,
      shared: true,
    },
    plotOptions: {
      series: {
        marker: { enabled: false, states: { hover: { enabled: !compact } } },
        lineWidth: compact ? 2 : 2.5,
        enableMouseTracking: !compact,
        animation: !compact,
      },
    },
    series: data.series.map((s, idx) => ({
      type: "line",
      name: s.name,
      data: s.data,
      color: idx === 0 ? c.primary : undefined,
    })),
  });

  return (
    <div className={compact ? "chart-sparkline" : "chart-wrap"}>
      <HighchartsReact highcharts={Highcharts} options={options} />
    </div>
  );
}
