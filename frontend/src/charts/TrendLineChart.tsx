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
