<script lang="ts">
	// Radar-style time-series trend, now backed by Chart.js (canvas) instead of a
	// hand-rolled SVG. Same public API as before — multi-series smoothed lines over a
	// timestamped x-axis, optional gradient area fill and dashed comparison lines.
	// Colours bridge to the CSS-variable theme and the chart is rebuilt on theme switch.
	import { parseTs } from '$lib/format';
	import { locale } from '$lib/i18n.svelte';
	import { theme } from '$lib/theme.svelte';
	import { ensureChart, chartTheme, resolveColor, withAlpha, Chart } from './chartjs';
	import type { Chart as ChartType } from 'chart.js';

	interface Series {
		label: string;
		color: string;
		values: number[];
		/** draw a gradient area under the line (primary series) */
		fill?: boolean;
		/** render as a dashed comparison line (e.g. previous period) */
		dash?: boolean;
	}

	let {
		series,
		timestamps,
		height = 200,
		zeroBased = false,
		format = (v: number) => v.toLocaleString(),
		leftAxisWidth
	}: {
		series: Series[];
		timestamps: (string | null)[];
		height?: number;
		zeroBased?: boolean;
		format?: (v: number) => string;
		/** force a fixed left-axis width (px) so this chart's plot area aligns with another stacked below it */
		leftAxisWidth?: number;
	} = $props();

	let canvas = $state<HTMLCanvasElement | null>(null);
	let chart: ChartType | null = null;

	const spanMs = () => {
		const a = parseTs(timestamps[0]);
		const b = parseTs(timestamps[timestamps.length - 1]);
		return a && b ? Math.abs(b.getTime() - a.getTime()) : 0;
	};

	function tickLabel(ts: string | null): string {
		const d = parseTs(ts);
		if (!d) return '';
		return spanMs() > 36 * 3600 * 1000
			? d.toLocaleDateString(locale.tag, { month: 'short', day: 'numeric' })
			: d.toLocaleTimeString(locale.tag, { hour: '2-digit', minute: '2-digit' });
	}
	function fullLabel(ts: string | null): string {
		const d = parseTs(ts);
		return d
			? d.toLocaleString(locale.tag, {
					month: 'short',
					day: 'numeric',
					hour: '2-digit',
					minute: '2-digit'
				})
			: '';
	}

	function build() {
		if (!canvas) return;
		ensureChart();
		const th = chartTheme();
		chart?.destroy();
		chart = new Chart(canvas, {
			type: 'line',
			data: {
				labels: timestamps.map((_, i) => i),
				datasets: series.map((s) => ({
					label: s.label,
					data: s.values,
					borderColor: resolveColor(s.color),
					borderWidth: s.dash ? 1.5 : 2,
					borderDash: s.dash ? [5, 4] : undefined,
					pointRadius: 0,
					pointHoverRadius: 3.5,
					pointHoverBorderColor: resolveColor(s.color),
					pointHoverBackgroundColor: resolveColor('var(--bg-elev)'),
					tension: 0.4,
					fill: s.fill ? 'origin' : false,
					backgroundColor: s.fill
						? (ctx: { chart: ChartType }) => {
								const area = ctx.chart.chartArea;
								if (!area) return 'transparent';
								const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
								g.addColorStop(0, withAlpha(s.color, 0.22));
								g.addColorStop(1, withAlpha(s.color, 0));
								return g;
							}
						: 'transparent'
				}))
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				animation: false,
				interaction: { mode: 'index', intersect: false },
				plugins: {
					legend: { display: false },
					tooltip: {
						backgroundColor: th.tooltipBg,
						titleColor: th.tooltipText,
						bodyColor: th.tooltipText,
						borderColor: th.tooltipBorder,
						borderWidth: 1,
						padding: 8,
						callbacks: {
							title: (items) => fullLabel(timestamps[items[0].dataIndex]),
							label: (item) => `${item.dataset.label}: ${format(item.parsed.y ?? 0)}`
						}
					}
				},
				scales: {
					x: {
						grid: { color: th.grid },
						ticks: {
							color: th.tick,
							maxRotation: 0,
							autoSkip: true,
							maxTicksLimit: 6,
							callback: (_v, i) => tickLabel(timestamps[i])
						}
					},
					y: {
						beginAtZero: zeroBased,
						afterFit: leftAxisWidth ? (s) => void (s.width = leftAxisWidth) : undefined,
						grid: { color: th.grid },
						ticks: { color: th.tick, maxTicksLimit: 3, callback: (v) => format(Number(v)) }
					}
				}
			}
		});
	}

	// Rebuild on data / format / theme change; theme.mode read so a light/dark switch
	// re-derives axis + tooltip colours.
	$effect(() => {
		void series;
		void timestamps;
		void zeroBased;
		void theme.mode;
		build();
		return () => {
			chart?.destroy();
			chart = null;
		};
	});
</script>

<div class="trend" style="height:{height}px">
	<canvas bind:this={canvas}></canvas>
</div>

<style>
	.trend {
		position: relative;
		width: 100%;
	}
	canvas {
		display: block;
	}
</style>
