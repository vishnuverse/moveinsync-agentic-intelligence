import Highcharts from "highcharts";
import type { ChartSeriesData } from "../api";
import { baseChartOptions, chartColors } from "./chartTheme";
import HighchartsReact from "./HighchartsReactCompat";
import "./charts.css";

interface BreakdownBarChartProps {
  data: ChartSeriesData;
  horizontal?: boolean;
  valuePrefix?: string;
  valueSuffix?: string;
  height?: number;
}

export function BreakdownBarChart({
  data,
  horizontal = false,
  valuePrefix = "",
  valueSuffix = "",
  height = 260,
}: BreakdownBarChartProps) {
  const c = chartColors();
  const options: Highcharts.Options = Highcharts.merge(baseChartOptions(), {
    chart: { type: horizontal ? "bar" : "column", height },
    xAxis: { categories: data.categories },
    yAxis: { labels: { format: `${valuePrefix}{value}${valueSuffix}` } },
    legend: { enabled: data.series.length > 1 },
    tooltip: { valuePrefix, valueSuffix, shared: true },
    plotOptions: {
      series: { borderRadius: 4, borderWidth: 0 },
      column: { groupPadding: 0.12, pointPadding: 0.05 },
      bar: { groupPadding: 0.12, pointPadding: 0.05 },
    },
    series: data.series.map((s, idx) => ({
      type: horizontal ? "bar" : "column",
      name: s.name,
      data: s.data,
      color: idx === 0 ? c.primary : idx === 1 ? c.secondary : undefined,
    })),
  });

  return (
    <div className="chart-wrap">
      <HighchartsReact highcharts={Highcharts} options={options} />
    </div>
  );
}
