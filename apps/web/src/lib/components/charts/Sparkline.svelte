<script lang="ts">
	// Sparkline / area trend, backed by Chart.js. Same API as the old SVG version:
	// fixed-size inline line with optional gradient fill, smoothing and hover readout.
	import ChartCanvas from './ChartCanvas.svelte';
	import { resolveColor, withAlpha, chartTheme } from './chartjs';
	import type { ChartConfiguration, Chart as ChartType } from 'chart.js';

	let {
		values,
		width = 120,
		height = 34,
		color = 'var(--c-accent)',
		fill = true,
		strokeWidth = 1.5,
		smooth = true,
		interactive = false,
		format
	}: {
		values: number[];
		width?: number;
		height?: number;
		color?: string;
		fill?: boolean;
		strokeWidth?: number;
		smooth?: boolean;
		interactive?: boolean;
		format?: (v: number) => string;
	} = $props();

	let config = $derived.by((): ChartConfiguration => {
		const th = chartTheme();
		return {
			type: 'line',
			data: {
				labels: values.map((_, i) => i),
				datasets: [
					{
						data: values,
						borderColor: resolveColor(color),
						borderWidth: strokeWidth,
						pointRadius: 0,
						pointHoverRadius: interactive ? 2.6 : 0,
						tension: smooth ? 0.4 : 0,
						fill: fill ? 'origin' : false,
						backgroundColor: fill
							? (ctx: { chart: ChartType }) => {
									const area = ctx.chart.chartArea;
									if (!area) return 'transparent';
									const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
									g.addColorStop(0, withAlpha(color, 0.28));
									g.addColorStop(1, withAlpha(color, 0));
									return g;
								}
							: 'transparent'
					}
				]
			},
			options: {
				responsive: false,
				maintainAspectRatio: false,
				animation: false,
				events: interactive ? ['mousemove', 'mouseout', 'touchstart', 'touchmove'] : [],
				interaction: { mode: 'index', intersect: false },
				plugins: {
					legend: { display: false },
					tooltip: interactive
						? {
								backgroundColor: th.tooltipBg,
								titleColor: th.tooltipText,
								bodyColor: th.tooltipText,
								borderColor: th.tooltipBorder,
								borderWidth: 1,
								padding: 6,
								displayColors: false,
								callbacks: {
									title: () => '',
									label: (item) =>
										format ? format(item.parsed.y ?? 0) : String(item.parsed.y ?? 0)
								}
							}
						: { enabled: false }
				},
				scales: { x: { display: false }, y: { display: false } }
			}
		};
	});
</script>

<ChartCanvas {config} {width} {height} fixed label="trend" />
