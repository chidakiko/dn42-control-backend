<script lang="ts">
	// Stacked vertical bar chart over time buckets, backed by Chart.js. Same API as
	// before: each group is a bar made of stacked parts (keyed; colour per key).
	import ChartCanvas from './ChartCanvas.svelte';
	import { chartTheme, resolveColor } from './chartjs';
	import type { ChartConfiguration } from 'chart.js';

	interface Part {
		key: string;
		value: number;
		color: string;
	}
	interface Group {
		label: string;
		parts: Part[];
	}
	let {
		groups,
		height = 130,
		maxLabels = 6,
		leftAxisWidth
	}: {
		groups: Group[];
		height?: number;
		maxLabels?: number;
		/** reserve a fixed (invisible) left gutter so the plot area aligns with a chart stacked above it */
		leftAxisWidth?: number;
	} = $props();

	let config = $derived.by((): ChartConfiguration => {
		const th = chartTheme();
		// Stable part-key order + colour (first-seen across groups).
		const order: string[] = [];
		const color: Record<string, string> = {};
		for (const g of groups)
			for (const p of g.parts)
				if (!(p.key in color)) {
					color[p.key] = p.color;
					order.push(p.key);
				}
		const datasets = order.map((k) => ({
			label: k,
			data: groups.map((g) => g.parts.find((p) => p.key === k)?.value ?? 0),
			backgroundColor: resolveColor(color[k]),
			stack: 's',
			borderRadius: 2,
			categoryPercentage: 0.9,
			barPercentage: 0.9
		}));
		return {
			type: 'bar',
			data: { labels: groups.map((g) => g.label), datasets },
			options: {
				responsive: true,
				maintainAspectRatio: false,
				animation: false,
				plugins: {
					legend: { display: false },
					tooltip: {
						backgroundColor: th.tooltipBg,
						titleColor: th.tooltipText,
						bodyColor: th.tooltipText,
						borderColor: th.tooltipBorder,
						borderWidth: 1,
						padding: 8
					}
				},
				scales: {
					x: {
						stacked: true,
						grid: { display: false },
						ticks: { color: th.tick, autoSkip: true, maxRotation: 0, maxTicksLimit: maxLabels }
					},
					y: leftAxisWidth
						? {
								stacked: true,
								beginAtZero: true,
								// reserve the same left gutter as the chart above, but draw nothing
								afterFit: (s) => void (s.width = leftAxisWidth),
								ticks: { display: false },
								grid: { display: false },
								border: { display: false }
							}
						: { stacked: true, display: false, beginAtZero: true }
				}
			}
		};
	});
</script>

<ChartCanvas {config} {height} label="bar chart" />
