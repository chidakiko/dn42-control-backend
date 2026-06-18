<script lang="ts">
	// Renders a coloured pill for a health/status value. Maps both the 4 fleet
	// health values and the backend report/apply status words to a colour class
	// and a localised label; anything unrecognised falls back to neutral + raw.
	import { t } from '$lib/i18n.svelte';

	// `muted` dims the badge to signal it's a stale / last-known value (e.g. the
	// report/apply status of a node that's now disconnected). `hint` is a tooltip.
	let {
		value,
		muted = false,
		hint = ''
	}: { value: string | null | undefined; muted?: boolean; hint?: string } = $props();

	const HEALTH: Record<string, string> = {
		ok: 'ok',
		stale: 'stale',
		degraded: 'degraded',
		down: 'down',
		unknown: 'unknown',
		bad: 'bad'
	};
	const STATUS: Record<string, string> = {
		succeeded: 'ok',
		failed: 'bad',
		skipped: 'unknown',
		running: 'ok',
		pending: 'stale'
	};

	let cls = $derived(
		!value ? 'neutral' : (HEALTH[value] ?? STATUS[value] ?? 'neutral')
	);
	let label = $derived.by(() => {
		if (!value) return '—';
		if (value in HEALTH) return t(`health.${value}`);
		if (value in STATUS) return t(`st.${value}`);
		return value;
	});
</script>

<span class="badge {cls}" class:dim={muted} title={hint}><span class="dot"></span>{label}</span>

<style>
	.dim {
		opacity: 0.45;
		filter: saturate(0.5);
	}
</style>
