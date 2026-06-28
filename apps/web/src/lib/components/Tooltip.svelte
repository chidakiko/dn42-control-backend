<script lang="ts">
	// Reusable Bits UI tooltip. The `trigger` snippet receives the props to spread on
	// your own element (so an <a>/<button> stays the real trigger). When `enabled` is
	// false the trigger renders bare (no tooltip) — handy for "only show when the
	// sidebar is collapsed". Requires a <Tooltip.Provider> high in the tree (layout).
	import { Tooltip } from 'bits-ui';
	import type { Snippet } from 'svelte';

	let {
		label,
		enabled = true,
		side = 'right',
		trigger
	}: {
		label: string;
		enabled?: boolean;
		side?: 'top' | 'right' | 'bottom' | 'left';
		trigger: Snippet<[Record<string, unknown>]>;
	} = $props();
</script>

{#if enabled}
	<Tooltip.Root delayDuration={150}>
		<Tooltip.Trigger>
			{#snippet child({ props })}{@render trigger(props)}{/snippet}
		</Tooltip.Trigger>
		<Tooltip.Portal>
			<Tooltip.Content {side} sideOffset={8} class="tt-content">
				{label}
				<Tooltip.Arrow />
			</Tooltip.Content>
		</Tooltip.Portal>
	</Tooltip.Root>
{:else}
	{@render trigger({})}
{/if}

<style>
	:global(.tt-content) {
		background: var(--text);
		color: var(--bg-elev);
		font-size: 0.78rem;
		font-weight: 500;
		padding: 0.3rem 0.55rem;
		border-radius: var(--radius-sm);
		box-shadow: var(--shadow);
		z-index: 600;
	}
</style>
