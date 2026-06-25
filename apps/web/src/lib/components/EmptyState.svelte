<script lang="ts">
	// Cloudflare-style empty state: centered icon + title + hint + optional CTA.
	// Use for "no data yet" placeholders (not for transient loading text).
	import Icon, { type IconName } from '$lib/components/Icon.svelte';

	let {
		icon = 'dashboard',
		title,
		hint = '',
		actionLabel = '',
		onaction
	}: {
		icon?: IconName;
		title: string;
		hint?: string;
		actionLabel?: string;
		onaction?: () => void;
	} = $props();
</script>

<div class="empty-state">
	<div class="es-icon"><Icon name={icon} size={34} stroke={1.5} /></div>
	<div class="es-title">{title}</div>
	{#if hint}<div class="es-hint">{hint}</div>{/if}
	{#if actionLabel && onaction}
		<button class="btn primary" style="margin-top:0.4rem" onclick={onaction}>{actionLabel}</button>
	{/if}
</div>
