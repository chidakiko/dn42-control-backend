<script lang="ts">
	import { toast } from '$lib/toast.svelte';
</script>

<div class="toaster">
	{#each toast.items as t (t.id)}
		<div class="toast {t.kind}" role="status">
			<span>{t.message}</span>
			<button class="x" aria-label="Dismiss" onclick={() => toast.dismiss(t.id)}>×</button>
		</div>
	{/each}
</div>

<style>
	.toaster {
		position: fixed;
		bottom: 1rem;
		right: 1rem;
		z-index: 1000;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		max-width: min(420px, 90vw);
	}
	.toast {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
		padding: 0.65rem 0.85rem;
		border-radius: var(--radius-sm);
		border: 1px solid var(--border-strong);
		background: var(--bg-elev-2);
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
		font-size: 0.85rem;
		animation: slidein 0.15s ease-out;
	}
	.toast.success {
		border-left: 3px solid var(--ok);
	}
	.toast.error {
		border-left: 3px solid var(--bad);
	}
	.toast.info {
		border-left: 3px solid var(--accent);
	}
	.x {
		background: none;
		border: none;
		color: var(--text-dim);
		cursor: pointer;
		font-size: 1.1rem;
		line-height: 1;
		padding: 0;
		width: auto;
	}
	@keyframes slidein {
		from {
			opacity: 0;
			transform: translateY(8px);
		}
	}
</style>
