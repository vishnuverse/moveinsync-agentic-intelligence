import Highcharts from "highcharts";
import type { ChartSeriesData } from "../api";
import { baseChartOptions } from "./chartTheme";
import { isChartSeriesEmpty } from "./chartHelpers";
import { ChartEmptyState } from "./ChartPanel";
import HighchartsReact from "./HighchartsReactCompat";
import "./charts.css";

interface StackedAreaChartProps {
  data: ChartSeriesData;
  valueSuffix?: string;
  height?: number;
}

export function StackedAreaChart({ data, valueSuffix = "", height = 260 }: StackedAreaChartProps) {
  if (isChartSeriesEmpty(data)) return <ChartEmptyState />;

  const options: Highcharts.Options = Highcharts.merge(baseChartOptions(), {
    chart: { type: "area", height },
    xAxis: {
      categories: data.categories,
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
