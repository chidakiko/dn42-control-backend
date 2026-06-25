<script lang="ts">
	// Donut chart: stacked arcs with rounded ends and a small gap between segments
	// for a cleaner, modern look. Hover a segment to highlight it and read its value
	// in the centre. Segments render clockwise from 12 o'clock.
	interface Seg {
		label: string;
		value: number;
		color: string;
	}
	let {
		segments,
		size = 150,
		thickness = 20,
		centerValue = '',
		centerLabel = '',
		half = false
	}: {
		segments: Seg[];
		size?: number;
		thickness?: number;
		centerValue?: string | number;
		centerLabel?: string;
		/** Render a top semicircle gauge (Radar-style) instead of a full ring. */
		half?: boolean;
	} = $props();

	// Reserve room so the hover-expanded stroke (thickness + 4) never clips the viewBox.
	let r = $derived((size - thickness) / 2 - 3);
	let circ = $derived(2 * Math.PI * r);
	// A half gauge fills only the top semicircle; crop the viewBox to that.
	let arcSpan = $derived(half ? circ / 2 : circ);
	let vbH = $derived(half ? Math.round(size / 2 + thickness / 2 + 2) : size);
	let total = $derived(segments.reduce((s, x) => s + x.value, 0));
	let nonZero = $derived(segments.filter((s) => s.value > 0).length);

	let arcs = $derived.by(() => {
		const gap = nonZero > 1 ? thickness : 0; // gap ≈ end-cap diameter
		let acc = 0;
		return segments.map((seg, i) => {
			const frac = total > 0 ? seg.value / total : 0;
			const len = frac * arcSpan;
			// Leave a gap after each segment; clamp so tiny slices still show as a dot.
			const draw = Math.max(len > 0 ? 1 : 0, len - gap);
			const arc = { ...seg, i, len, draw, gap: circ - draw, offset: -acc };
			acc += len;
			return arc;
		});
	});

	let hover = $state<number | null>(null);
	let center = $derived.by(() => {
		if (hover != null && segments[hover]) {
			return { value: segments[hover].value, label: segments[hover].label };
		}
		return { value: centerValue, label: centerLabel };
	});
</script>

<svg
	viewBox="0 0 {size} {vbH}"
	width={size}
	height={vbH}
	class="donut"
	role="img"
	onpointerleave={() => (hover = null)}
>
	<g transform={half ? `rotate(180 ${size / 2} ${size / 2})` : `rotate(-90 ${size / 2} ${size / 2})`}>
		<circle
			cx={size / 2}
			cy={size / 2}
			{r}
			fill="none"
			stroke="var(--bg-elev-2)"
			stroke-width={thickness}
			stroke-dasharray={half ? `${circ / 2} ${circ / 2}` : undefined}
		/>
		{#each arcs as a (a.label)}
			{#if a.draw > 0}
				<circle
					cx={size / 2}
					cy={size / 2}
					{r}
					fill="none"
					stroke={a.color}
					stroke-width={hover === a.i ? thickness + 4 : thickness}
					stroke-linecap="round"
					stroke-dasharray="{a.draw} {a.gap}"
					stroke-dashoffset={a.offset}
					opacity={hover != null && hover !== a.i ? 0.35 : 1}
					onpointerenter={() => (hover = a.i)}
					role="presentation"
					style="transition: stroke-width 0.12s, opacity 0.12s; cursor: default"
				>
					<title>{a.label}: {a.value}</title>
				</circle>
			{/if}
		{/each}
	</g>
	{#if !half}
		{#if center.value !== ''}
			<text x={size / 2} y={size / 2} text-anchor="middle" class="cv">{center.value}</text>
			<text x={size / 2} y={size / 2 + 18} text-anchor="middle" class="cl">{center.label}</text>
		{:else if center.label !== ''}
			<text x={size / 2} y={size / 2} text-anchor="middle" class="cl cl-center">{center.label}</text>
		{/if}
	{/if}
</svg>

<style>
	.donut {
		display: block;
	}
	.cv {
		fill: var(--text);
		font-size: 1.6rem;
		font-weight: 700;
		dominant-baseline: middle;
		font-variant-numeric: tabular-nums;
	}
	.cl {
		fill: var(--text-faint);
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.cl-center {
		dominant-baseline: middle;
		font-size: 0.82rem;
		fill: var(--text-dim);
	}
</style>
