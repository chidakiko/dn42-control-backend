<script lang="ts">
	import type { Snippet } from 'svelte';

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

	function close() {
		open = false;
		onclose?.();
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}
</script>

<svelte:window on:keydown={onkeydown} />

{#if open}
	<div
		class="backdrop"
		role="button"
		tabindex="-1"
		onclick={(e) => {
			if (e.target === e.currentTarget) close();
		}}
		onkeydown={() => {}}
	>
		<div class="dialog card" role="dialog" aria-modal="true" aria-label={title}>
			<div class="card-head">
				<h2>{title}</h2>
				<button class="btn ghost sm" onclick={close} aria-label="Close">×</button>
			</div>
			<div class="body">
				{@render children()}
			</div>
			{#if footer}
				<div class="foot">
					{@render footer()}
				</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.6);
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding: 4vh 1rem;
		z-index: 500;
		overflow-y: auto;
	}
	.dialog {
		width: 100%;
		max-width: 640px;
		margin: auto 0;
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
