<script lang="ts">
	// Shared Chart.js host: owns the <canvas>, (re)creates the chart whenever the
	// `config` object changes (parents pass a $derived config so data/format changes
	// trigger a rebuild) or the theme switches (so axis/tooltip colours re-derive).
	import { theme } from '$lib/theme.svelte';
	import { ensureChart, Chart } from './chartjs';
	import type { ChartConfiguration, Chart as ChartType } from 'chart.js';

	let {
		config,
		height,
		width,
		fixed = false,
		label = 'chart'
	}: {
		config: ChartConfiguration;
		height?: number;
		width?: number;
		/** fixed-size canvas (sparkline/minibar) vs responsive-to-container (line/bar/donut) */
		fixed?: boolean;
		label?: string;
	} = $props();

	let canvas = $state<HTMLCanvasElement | null>(null);
	let chart: ChartType | null = null;

	$effect(() => {
		void config;
		void theme.mode;
		if (!canvas) return;
		ensureChart();
		chart?.destroy();
		chart = new Chart(canvas, config);
		return () => {
			chart?.destroy();
			chart = null;
		};
	});
</script>

{#if fixed}
	<canvas bind:this={canvas} {width} {height} aria-label={label}></canvas>
{:else}
	<div class="cc" style:height={height ? `${height}px` : undefined}>
		<canvas bind:this={canvas} aria-label={label}></canvas>
	</div>
{/if}

<style>
	.cc {
		position: relative;
		width: 100%;
	}
	canvas {
		display: block;
	}
</style>
