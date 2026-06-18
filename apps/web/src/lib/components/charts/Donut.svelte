<script lang="ts">
	// Donut chart built from stacked stroke-dasharray arcs. Segments render
	// clockwise from 12 o'clock; an optional centre value/label sits in the hole.
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
		centerLabel = ''
	}: {
		segments: Seg[];
		size?: number;
		thickness?: number;
		centerValue?: string | number;
		centerLabel?: string;
	} = $props();

	let r = $derived((size - thickness) / 2);
	let circ = $derived(2 * Math.PI * r);
	let total = $derived(segments.reduce((s, x) => s + x.value, 0));

	let arcs = $derived.by(() => {
		let acc = 0;
		return segments.map((seg) => {
			const frac = total > 0 ? seg.value / total : 0;
			const len = frac * circ;
			const arc = { ...seg, len, gap: circ - len, offset: -acc };
			acc += len;
			return arc;
		});
	});
</script>

<svg viewBox="0 0 {size} {size}" width={size} height={size} class="donut">
	<g transform="rotate(-90 {size / 2} {size / 2})">
		<circle
			cx={size / 2}
			cy={size / 2}
			{r}
			fill="none"
			stroke="var(--bg-elev-2)"
			stroke-width={thickness}
		/>
		{#each arcs as a (a.label)}
			{#if a.len > 0.01}
				<circle
					cx={size / 2}
					cy={size / 2}
					{r}
					fill="none"
					stroke={a.color}
					stroke-width={thickness}
					stroke-dasharray="{a.len} {a.gap}"
					stroke-dashoffset={a.offset}
				>
					<title>{a.label}: {a.value}</title>
				</circle>
			{/if}
		{/each}
	</g>
	{#if centerValue !== ''}
		<text x={size / 2} y={size / 2} text-anchor="middle" class="cv">{centerValue}</text>
		<text x={size / 2} y={size / 2 + 18} text-anchor="middle" class="cl">{centerLabel}</text>
	{:else if centerLabel !== ''}
		<text x={size / 2} y={size / 2} text-anchor="middle" class="cl cl-center">{centerLabel}</text>
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
	}
	.cl {
		fill: var(--text-faint);
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	/* label-only centre (no value): vertically centred, a touch larger */
	.cl-center {
		dominant-baseline: middle;
		font-size: 0.82rem;
		fill: var(--text-dim);
	}
</style>
