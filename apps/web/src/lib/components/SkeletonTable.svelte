<script lang="ts">
	// Shape-matched loading placeholder for a data table: optional header row + N
	// skeleton rows whose cell widths mirror the real columns, so the loading state
	// crossfades seamlessly into the table.
	import Skeleton from './Skeleton.svelte';

	let {
		headers = [],
		cols,
		rows = 6
	}: {
		/** optional header labels; omit to skip the <thead> */
		headers?: string[];
		/** per-column cell width (CSS length); length defines the column count */
		cols: string[];
		rows?: number;
	} = $props();
</script>

<table>
	{#if headers.length}
		<thead>
			<tr>{#each headers as h, i (i)}<th>{h}</th>{/each}</tr>
		</thead>
	{/if}
	<tbody>
		{#each Array(rows) as _, r (r)}
			<tr>
				{#each cols as w, c (c)}<td><Skeleton {w} h="0.85rem" /></td>{/each}
			</tr>
		{/each}
	</tbody>
</table>
