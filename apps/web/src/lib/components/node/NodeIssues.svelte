<script lang="ts">
	// Resident "current issues" panel. Prefers drift piped from the parent's
	// /overview fetch (one request for the whole page); falls back to self-fetching
	// the latest report event when used standalone (drift prop omitted).
	import { untrack } from 'svelte';
	import { api } from '$lib/api';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t } from '$lib/i18n.svelte';
	import { fmtTime } from '$lib/format';
	import type { StatusEvent, DriftItem } from '$lib/types';
	import DriftCards from '../DriftCards.svelte';

	let {
		nodeId,
		drift,
		asOf
	}: { nodeId: string; drift?: DriftItem[]; asOf?: string | null } = $props();

	let usePiped = $derived(drift !== undefined);

	// self-fetch fallback (standalone use)
	let selfLatest = $state<StatusEvent | null>(null);
	let selfLoaded = $state(false);

	async function load() {
		try {
			const r = await api.statusEvents(nodeId, 'report', 1);
			selfLatest = r.events[0] ?? null;
		} catch {
			/* best-effort */
		} finally {
			selfLoaded = true;
		}
	}
	$effect(() => {
		autoRefresh.tick;
		nodeId; // reload when the node changes
		if (!usePiped) untrack(() => load());
	});

	let items = $derived.by((): DriftItem[] => {
		if (usePiped) return drift ?? [];
		const d = (selfLatest?.payload as { drift?: unknown })?.drift;
		return Array.isArray(d) ? (d as DriftItem[]) : [];
	});
	let reportedAt = $derived(usePiped ? (asOf ?? null) : (selfLatest?.created_at ?? null));
	let ready = $derived(usePiped || selfLoaded);
	let everReported = $derived(usePiped ? asOf != null : selfLatest !== null);
</script>

{#if ready}
	<section class="issues">
		<div class="ihead">
			<h4>
				{t('issues.title')}
				{#if items.length}<span class="count">{items.length}</span>{/if}
			</h4>
			{#if reportedAt}<span class="when faint">{t('issues.asOf')} {fmtTime(reportedAt)}</span>{/if}
		</div>
		{#if !everReported}
			<div class="state faint">{t('issues.noReport')}</div>
		{:else if items.length === 0}
			<div class="state clean"><span class="tick">✓</span>{t('issues.clean')}</div>
		{:else}
			<DriftCards {items} />
		{/if}
	</section>
{/if}

<style>
	.issues {
		margin-bottom: 1.25rem;
	}
	.ihead {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.6rem;
	}
	.ihead h4 {
		margin: 0;
		font-size: 0.95rem;
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
	}
	.count {
		font-size: 0.72rem;
		font-weight: 700;
		color: var(--bad);
		background: color-mix(in srgb, var(--bad) 14%, transparent);
		border-radius: 999px;
		padding: 0.05rem 0.45rem;
	}
	.when {
		font-size: 0.75rem;
	}
	.state {
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		background: var(--bg-elev);
		padding: 0.6rem 0.75rem;
		font-size: 0.85rem;
	}
	.state.clean {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--ok);
		border-color: color-mix(in srgb, var(--ok) 35%, var(--border));
		background: color-mix(in srgb, var(--ok) 8%, transparent);
	}
	.tick {
		font-weight: 700;
	}
</style>
