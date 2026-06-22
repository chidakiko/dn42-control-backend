<script lang="ts">
	// Inline magnitude bar for table cells: a track + proportional fill, with the
	// value shown alongside. Used for drift counts, session counts, etc.
	let {
		value,
		max,
		color = 'var(--c-accent)',
		showValue = true
	}: { value: number; max: number; color?: string; showValue?: boolean } = $props();

	let pct = $derived(max > 0 ? Math.min(100, Math.max(value > 0 ? 4 : 0, (value / max) * 100)) : 0);
</script>

<span class="minibar">
	{#if showValue}<span class="val mono">{value}</span>{/if}
	<span class="track"><span class="fill" style="width:{pct}%; background:{color}"></span></span>
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
	.fill {
		position: absolute;
		inset: 0 auto 0 0;
		border-radius: 3px;
	}
</style>
