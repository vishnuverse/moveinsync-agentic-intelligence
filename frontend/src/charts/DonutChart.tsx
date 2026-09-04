import Highcharts from "highcharts";
import type { PieChartData } from "../api";
import { baseChartOptions, chartColors } from "./chartTheme";
import HighchartsReact from "./HighchartsReactCompat";
import "./charts.css";

interface DonutChartProps {
  data: PieChartData;
  height?: number;
}

export function DonutChart({ data, height = 260 }: DonutChartProps) {
  const c = chartColors();
  const palette = [c.primary, c.secondary, c.warning, c.critical, c.textMuted];
  const series = data.series[0];

  const options: Highcharts.Options = Highcharts.merge(baseChartOptions(), {
    chart: { type: "pie", height },
    tooltip: { pointFormat: "<b>{point.y}</b> ({point.percentage:.1f}%)" },
    plotOptions: {
      pie: {
        innerSize: "62%",
        borderWidth: 2,
        borderColor: c.surface,
        dataLabels: {
          enabled: true,
          format: "{point.name}: {point.percentage:.0f}%",
          style: { color: c.text, fontSize: "11px", textOutline: "none" },
        },
      },
    },
    series: [
      {
        type: "pie",
        name: series?.name ?? "",
        data: (series?.data ?? []).map((slice, idx) => ({
          name: slice.name,
          y: slice.y,
          color: palette[idx % palette.length],
        })),
      },
    ],
  });

  return (
    <div className="chart-wrap">
      <HighchartsReact highcharts={Highcharts} options={options} />
    </div>
  );
}
