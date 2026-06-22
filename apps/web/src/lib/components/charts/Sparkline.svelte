<script lang="ts">
	// Minimal sparkline: a scaled polyline with an optional soft area fill.
	let {
		values,
		width = 120,
		height = 34,
		color = 'var(--c-accent)',
		fill = true,
		strokeWidth = 1.5
	}: {
		values: number[];
		width?: number;
		height?: number;
		color?: string;
		fill?: boolean;
		strokeWidth?: number;
	} = $props();

	let paths = $derived.by(() => {
		if (!values || values.length === 0) return { line: '', area: '' };
		const n = values.length;
		const min = Math.min(...values);
		const max = Math.max(...values);
		const span = max - min || 1;
		const pad = 2;
		const x = (i: number) => pad + (i * (width - 2 * pad)) / Math.max(1, n - 1);
		const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad);
		const line = values
			.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
			.join(' ');
		const area = `${line} L${x(n - 1).toFixed(1)} ${height} L${x(0).toFixed(1)} ${height} Z`;
		return { line, area };
	});
</script>

<svg viewBox="0 0 {width} {height}" {width} {height} class="spark" preserveAspectRatio="none">
	{#if fill && paths.area}
		<path d={paths.area} fill={color} opacity="0.13" />
	{/if}
	<path
		d={paths.line}
		fill="none"
		stroke={color}
		stroke-width={strokeWidth}
		stroke-linejoin="round"
		stroke-linecap="round"
		vector-effect="non-scaling-stroke"
	/>
</svg>

<style>
	.spark {
		display: block;
	}
</style>
