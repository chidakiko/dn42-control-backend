<script lang="ts">
	// Internal interconnect panel: iBGP + OSPF are synthesised from
	// bird.internal_topology (not bgp_sessions records), so they never show under
	// "BGP sessions". This surfaces the configured topology plus a routing-derived
	// liveness hint (rib_routes = best-path routes the protocol contributes).
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { t } from '$lib/i18n.svelte';
	import { fmtTime } from '$lib/format';
	import { autoRefresh } from '$lib/refresh.svelte';
	import type { InternalTopologyView } from '$lib/types';

	let { nodeId }: { nodeId: string } = $props();

	let data = $state<InternalTopologyView | null>(null);
	let loading = $state(true);
	let err = $state('');

	async function load() {
		if (!data) loading = true;
		err = '';
		try {
			data = await api.nodeInternalTopology(nodeId);
		} catch (e) {
			data = null;
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}

	// Refresh on each tick + when navigating to another node; untrack the loader
	// so its own writes don't re-trigger the effect.
	$effect(() => {
		autoRefresh.tick;
		nodeId;
		untrack(() => load());
	});

	const liveBadge = (inRib: boolean) => (inRib ? 'ok' : 'neutral');
</script>

<div class="card-head">
	<h3>{t('internal.title')}</h3>
	<button class="btn sm" onclick={load} disabled={loading}>↻</button>
</div>

{#if loading && !data}
	<div class="empty">{t('common.loading')}</div>
{:else if err}
	<p class="error-text">{err}</p>
{:else if !data || !data.configured}
	<div class="empty">{t('internal.empty')}</div>
{:else}
	<p class="note">{t('internal.note')}</p>

	<div class="meta">
		{#if data.full_mesh_ibgp}<span class="tag">{t('internal.mesh')}</span>{/if}
		{#if data.routing_observed && data.captured_at}
			<span class="faint">{t('internal.captured')} {fmtTime(data.captured_at)}</span>
		{:else}
			<span class="faint">{t('internal.notObserved')}</span>
		{/if}
	</div>

	<h4>{t('internal.ibgp')} ({data.ibgp_peers.length})</h4>
	{#if data.ibgp_peers.length}
		<table>
			<thead>
				<tr>
					<th>{t('internal.peer')}</th>
					<th>{t('internal.loopback')}</th>
					<th>{t('internal.protocol')}</th>
					<th>{t('internal.ribRoutes')}</th>
				</tr>
			</thead>
			<tbody>
				{#each data.ibgp_peers as p (p.protocol)}
					<tr>
						<td class="mono">{p.node}</td>
						<td class="mono faint">{p.ownip6}</td>
						<td class="mono">{p.protocol}</td>
						<td>
							<span class="badge {liveBadge(p.in_rib)}">
								<span class="dot"></span>{p.rib_routes}
								{p.in_rib ? t('internal.live') : t('internal.notLive')}
							</span>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="faint">{t('common.none')}</p>
	{/if}

	<h4>{t('internal.ospf')}</h4>
	<div class="inline ospf-badges">
		{#each data.ospf as o (o.protocol)}
			<span class="badge {liveBadge(o.in_rib)}">
				<span class="dot"></span>{o.protocol} · {o.rib_routes}
			</span>
		{:else}
			<span class="faint">{t('common.none')}</span>
		{/each}
	</div>

	{#if data.ospf_neighbors.length}
		<h4>{t('internal.ospfNeighbors')}</h4>
		<table>
			<thead>
				<tr>
					<th>{t('internal.peer')}</th>
					<th>{t('internal.iface')}</th>
					<th>{t('internal.cost')}</th>
				</tr>
			</thead>
			<tbody>
				{#each data.ospf_neighbors as n (n.node + (n.interface ?? ''))}
					<tr>
						<td class="mono">{n.node}</td>
						<td class="mono">{n.interface ?? '—'}</td>
						<td class="mono">{n.cost ?? '—'}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}

	<p class="faint note-sm">{t('internal.livenessNote')}</p>
{/if}

<style>
	.note {
		margin: 0 0 0.75rem;
		padding: 0.6rem 0.85rem;
		border-radius: var(--radius);
		background: var(--surface-2, rgba(127, 127, 127, 0.08));
		color: var(--text-dim);
		font-size: 0.85rem;
		line-height: 1.5;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin-bottom: 0.5rem;
		font-size: 0.8rem;
	}
	h4 {
		margin: 1.1rem 0 0.4rem;
		font-size: 0.9rem;
	}
	.ospf-badges {
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.note-sm {
		margin-top: 1.25rem;
		font-size: 0.78rem;
		line-height: 1.5;
	}
</style>
