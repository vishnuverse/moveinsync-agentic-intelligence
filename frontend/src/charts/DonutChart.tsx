import Highcharts from "highcharts";
import type { PieChartData } from "../api";
import { baseChartOptions, chartColors } from "./chartTheme";
import { isPieEmpty } from "./chartHelpers";
import { ChartEmptyState } from "./ChartPanel";
import HighchartsReact from "./HighchartsReactCompat";
import "./charts.css";

interface DonutChartProps {
  data: PieChartData;
  height?: number;
}

export function DonutChart({ data, height = 260 }: DonutChartProps) {
  if (isPieEmpty(data)) return <ChartEmptyState />;

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
          // A zero-value slice has no arc for its leader line to anchor
          // to -- Highcharts still tries to place a "0%" label, which reads
          // as orphaned/floating (found live on the No-Show Cause Split
          // panel). A formatter lets zero slices opt out of a label
          // entirely instead of the fixed `format` string rendering one
          // for every slice regardless of value.
          formatter(this: Highcharts.Point) {
            if (!this.y) return null;
            const pct = (this as unknown as { percentage?: number }).percentage ?? 0;
            return `${this.name}: ${Highcharts.numberFormat(pct, 0)}%`;
          },
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
