<script lang="ts">
	// Shows a one-time secret in a modal with a copy button. The control server
	// only returns these once (agent tokens, enrollment tokens), so we make it
	// obvious and easy to copy. Binding `secret` to null closes it.
	import Modal from './Modal.svelte';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';

	let {
		secret = $bindable(null),
		label = 'Secret (shown once)'
	}: { secret?: string | null; label?: string } = $props();

	let open = $state(false);

	// Keep the modal open state in sync with the presence of a secret.
	$effect(() => {
		open = secret !== null;
	});

	function onclose() {
		secret = null;
	}

	async function copy() {
		if (!secret) return;
		try {
			await navigator.clipboard.writeText(secret);
			toast.success(t('common.copied'));
		} catch {
			toast.error(t('common.copyBlocked'));
		}
	}
</script>

<Modal title={t('secret.title')} bind:open {onclose}>
	<p class="muted">{t('secret.body', label)}</p>
	<div class="secret">
		<code>{secret}</code>
	</div>
	{#snippet footer()}
		<button class="btn" onclick={onclose}>{t('common.close')}</button>
		<button class="btn primary" onclick={copy}>{t('common.copy')}</button>
	{/snippet}
</Modal>

<style>
	.secret {
		background: var(--bg);
		border: 1px solid var(--border-strong);
		border-radius: var(--radius-sm);
		padding: 0.85rem;
		margin: 0.85rem 0;
		word-break: break-all;
	}
	.secret code {
		font-size: 0.85rem;
		color: var(--ok);
	}
</style>
