import Highcharts from "highcharts";
import type { ChartSeriesData } from "../api";
import { baseChartOptions } from "./chartTheme";
import HighchartsReact from "./HighchartsReactCompat";
import "./charts.css";

interface StackedAreaChartProps {
  data: ChartSeriesData;
  valueSuffix?: string;
  height?: number;
}

export function StackedAreaChart({ data, valueSuffix = "", height = 260 }: StackedAreaChartProps) {
  const options: Highcharts.Options = Highcharts.merge(baseChartOptions(), {
    chart: { type: "area", height },
    xAxis: {
      categories: data.categories,
      labels: { step: Math.max(1, Math.floor(data.categories.length / 8)) },
    },
    yAxis: { labels: { format: `{value}${valueSuffix}` } },
    tooltip: { valueSuffix, shared: true },
    plotOptions: {
      area: {
        stacking: "normal",
        marker: { enabled: false },
        lineWidth: 1,
        fillOpacity: 0.75,
      },
    },
    series: data.series.map((s) => ({ type: "area", name: s.name, data: s.data })),
  });

  return (
    <div className="chart-wrap">
      <HighchartsReact highcharts={Highcharts} options={options} />
    </div>
  );
}
