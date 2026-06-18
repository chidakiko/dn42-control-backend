<script lang="ts">
	// Stacked vertical bar chart over time buckets. Each group is one bar made of
	// stacked parts (e.g. apply succeeded vs failed). Pure CSS flex bars; a sparse
	// set of x-axis labels keeps it readable.
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
		maxLabels = 6
	}: { groups: Group[]; height?: number; maxLabels?: number } = $props();

	let max = $derived(
		Math.max(1, ...groups.map((g) => g.parts.reduce((s, p) => s + p.value, 0)))
	);
	let labelEvery = $derived(Math.max(1, Math.ceil(groups.length / maxLabels)));
</script>

<div class="chart">
	<div class="plot" style="height:{height}px">
		{#each groups as g, i (i)}
			{@const tot = g.parts.reduce((s, p) => s + p.value, 0)}
			<div class="col">
				<div class="bar" title="{g.label} · {tot}">
					{#each g.parts as p (p.key)}
						{#if p.value > 0}
							<div class="seg" style="height:{(p.value / max) * 100}%; background:{p.color}"></div>
						{/if}
					{/each}
				</div>
			</div>
		{/each}
	</div>
	<div class="axis">
		{#each groups as g, i (i)}
			<div class="lbl">{i % labelEvery === 0 ? g.label : ''}</div>
		{/each}
	</div>
</div>

<style>
	.chart {
		width: 100%;
	}
	.plot {
		display: flex;
		align-items: flex-end;
		gap: 3px;
		border-bottom: 1px solid var(--border);
	}
	.col {
		flex: 1;
		min-width: 0;
		height: 100%;
		display: flex;
		align-items: flex-end;
	}
	.bar {
		width: 100%;
		height: 100%; /* definite height so segment % heights resolve */
		display: flex;
		flex-direction: column-reverse; /* first part sits at the bottom */
		justify-content: flex-start;
		border-radius: 3px 3px 0 0;
		overflow: hidden;
	}
	.seg {
		width: 100%;
	}
	.axis {
		display: flex;
		gap: 3px;
		margin-top: 4px;
	}
	.lbl {
		flex: 1;
		min-width: 0;
		text-align: center;
		font-size: 0.62rem;
		color: var(--text-faint);
		white-space: nowrap;
		overflow: hidden;
	}
</style>
