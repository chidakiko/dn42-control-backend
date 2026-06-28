<script lang="ts">
	// Shape-matched loading placeholder for text/card blocks: a stack of skeleton
	// lines (last one shortened) to stand in for paragraphs / key-value rows / panels.
	import Skeleton from './Skeleton.svelte';

	let {
		lines = 3,
		gap = '0.6rem',
		height = '1rem'
	}: { lines?: number; gap?: string; height?: string } = $props();
</script>

<div class="sk-stack" style:gap>
	{#each Array(lines) as _, i (i)}
		<Skeleton w={i === lines - 1 ? '55%' : '100%'} h={height} />
	{/each}
</div>

<style>
	.sk-stack {
		display: flex;
		flex-direction: column;
	}
</style>
