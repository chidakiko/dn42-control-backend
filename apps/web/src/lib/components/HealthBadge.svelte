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
		hint = '',
		compact = false
	}: { value: string | null | undefined; muted?: boolean; hint?: string; compact?: boolean } =
		$props();

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

{#if compact}
	<span class="compact" class:dim={muted} title={hint || label}>
		<span class="cdot {cls}"></span>{label}
	</span>
{:else}
	<span class="badge {cls}" class:dim={muted} title={hint}><span class="dot"></span>{label}</span>
{/if}

<style>
	.dim {
		opacity: 0.45;
		filter: saturate(0.5);
	}
	/* compact: coloured status dot + muted text, no pill — for dense table cells */
	.compact {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.82rem;
		color: var(--text-dim);
		white-space: nowrap;
	}
	.cdot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		flex: none;
		background: var(--unknown);
	}
	.cdot.ok {
		background: var(--ok);
	}
	.cdot.stale {
		background: var(--warn);
	}
	.cdot.degraded,
	.cdot.bad {
		background: var(--bad);
	}
	.cdot.down {
		background: var(--down);
	}
	.cdot.unknown,
	.cdot.neutral {
		background: var(--unknown);
	}
</style>
