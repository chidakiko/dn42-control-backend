<script lang="ts">
	// Inline magnitude bar for table cells: value text + a proportional fill over a
	// track. Now drawn with Chart.js (a single horizontal bar) over a CSS track.
	import ChartCanvas from './ChartCanvas.svelte';
	import { resolveColor } from './chartjs';
	import type { ChartConfiguration } from 'chart.js';

	let {
		value,
		max,
		color = 'var(--c-accent)',
		showValue = true
	}: { value: number; max: number; color?: string; showValue?: boolean } = $props();

	let config = $derived.by((): ChartConfiguration => ({
		type: 'bar',
		data: {
			labels: [''],
			datasets: [
				{
					data: [value],
					backgroundColor: resolveColor(color),
					borderRadius: 3,
					barThickness: 6
				}
			]
		},
		options: {
			indexAxis: 'y',
			responsive: true,
			maintainAspectRatio: false,
			animation: false,
			events: [],
			plugins: { legend: { display: false }, tooltip: { enabled: false } },
			layout: { padding: 0 },
			scales: {
				x: { display: false, min: 0, max: max > 0 ? max : 1 },
				y: { display: false }
			}
		}
	}));
</script>

<span class="minibar">
	{#if showValue}<span class="val mono">{value}</span>{/if}
	<span class="track">
		<ChartCanvas {config} height={6} label="magnitude" />
	</span>
</span>

<style>
	.minibar {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		min-width: 90px;
	}
	.val {
		min-width: 1.2em;
		text-align: right;
		color: var(--text);
	}
	.track {
		position: relative;
		flex: 1;
		height: 6px;
		border-radius: 3px;
		background: var(--bg-elev-2);
		overflow: hidden;
	}
</style>
