<script lang="ts">
	// Thin wrapper over Bits UI's headless Dialog: keeps the project's card styling
	// and the original public API ({ title, open (bindable), onclose, children, footer }),
	// but delegates focus-trap / scroll-lock / ESC / aria to the accessible primitive
	// instead of hand-rolling them. Consumers are unchanged.
	import type { Snippet } from 'svelte';
	import { Dialog } from 'bits-ui';

	let {
		title,
		open = $bindable(),
		onclose,
		children,
		footer
	}: {
		title: string;
		open: boolean;
		onclose?: () => void;
		children: Snippet;
		footer?: Snippet;
	} = $props();
</script>

<Dialog.Root
	bind:open
	onOpenChange={(v) => {
		if (!v) onclose?.();
	}}
>
	<Dialog.Overlay class="modal-backdrop" />
	<Dialog.Content class="modal-dialog card" aria-label={title}>
		<div class="card-head">
			<Dialog.Title>
				{#snippet child({ props })}<h2 {...props}>{title}</h2>{/snippet}
			</Dialog.Title>
			<Dialog.Close aria-label="Close">
				{#snippet child({ props })}<button {...props} class="btn ghost sm">×</button>{/snippet}
			</Dialog.Close>
		</div>
		<div class="body">
			{@render children()}
		</div>
		{#if footer}
			<div class="foot">
				{@render footer()}
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>

<style>
	/* Bits UI keeps Overlay + Content as siblings; :global because the dialog may be
	   portaled out of this component's scope. Styling/look matches the old Modal. */
	:global(.modal-backdrop) {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.6);
		z-index: 500;
	}
	:global(.modal-dialog) {
		position: fixed;
		left: 50%;
		top: 4vh;
		transform: translateX(-50%);
		width: calc(100% - 2rem);
		max-width: 640px;
		max-height: 92vh;
		overflow-y: auto;
		z-index: 501;
	}
	.body {
		max-height: 70vh;
		overflow-y: auto;
	}
	.foot {
		display: flex;
		justify-content: flex-end;
		gap: 0.6rem;
		margin-top: 1rem;
		padding-top: 1rem;
		border-top: 1px solid var(--border);
	}
</style>
