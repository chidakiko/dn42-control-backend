<script lang="ts">
	// Fleet-wide routing overview for the dashboard: total table size across all
	// nodes, IPv4/IPv6 split, RPKI validity, and a compact per-node breakdown.
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import type { FleetRouting } from '$lib/types';
	import Donut from '$lib/components/charts/Donut.svelte';
	import MiniBar from '$lib/components/charts/MiniBar.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { relTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';

	let data = $state<FleetRouting | null>(null);
	let loading = $state(true);
	let error = $state('');

	async function load() {
		if (!data) loading = true;
		error = '';
		try {
			data = await api.routingFleet();
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		autoRefresh.tick;
		untrack(() => load());
	});

	const V4_COLOR = 'var(--c-accent)';
	const V6_COLOR = 'var(--c-ok)';

	let familySegments = $derived(
		data
			? [
					{ label: t('routing.v4'), value: data.summary.route_count_v4, color: V4_COLOR },
					{ label: t('routing.v6'), value: data.summary.route_count_v6, color: V6_COLOR }
				]
			: []
	);

	let rpkiSegments = $derived(
		data
			? [
					{ label: t('routing.rpki.valid'), value: data.summary.rpki.valid, color: 'var(--c-ok)' },
					{ label: t('routing.rpki.invalid'), value: data.summary.rpki.invalid, color: 'var(--c-bad)' },
					{ label: t('routing.rpki.not_found'), value: data.summary.rpki.not_found, color: 'var(--c-warn)' }
				]
			: []
	);

	// RPKI 甜甜圈中心：有效率%（让人一眼知道这是 RPKI 且多少前缀有效）。
	let rpkiValidPct = $derived.by(() => {
		const r = data?.summary.rpki;
		if (!r) return '';
		const tot = r.valid + r.invalid + r.not_found;
		return tot > 0 ? Math.round((r.valid / tot) * 100) + '%' : '';
	});

	let maxRoutes = $derived(data ? Math.max(1, ...data.nodes.map((n) => n.route_count)) : 1);
</script>

<div class="spread head">
	<h2>{t('dash.routing.title')}</h2>
	{#if data}<span class="faint">{t('dash.routing.reporting', data.summary.nodes_reporting)}</span>{/if}
</div>

{#if loading && !data}
	<div class="card"><div class="empty">{t('common.loading')}</div></div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if data && data.summary.nodes_reporting === 0}
	<div class="card"><div class="empty">{t('dash.routing.empty')}</div></div>
{:else if data}
	<div class="card routing-card">
		<div class="donut-wrap">
			<Donut
				segments={familySegments}
				size={150}
				thickness={22}
				centerValue={data.summary.route_count}
				centerLabel={t('routing.routes')}
			/>
			<div class="legend">
				{#each familySegments as s (s.label)}
					<span><span class="sw" style="background:{s.color}"></span>{s.label} <b>{s.value}</b></span>
				{/each}
			</div>
		</div>
		<div class="donut-wrap">
			<Donut
				segments={rpkiSegments}
				size={150}
				thickness={22}
				centerValue={rpkiValidPct}
				centerLabel={t('routing.rpki.center')}
			/>
			<div class="legend">
				{#each rpkiSegments as s (s.label)}
					<span><span class="sw" style="background:{s.color}"></span>{s.label} <b>{s.value}</b></span>
				{/each}
			</div>
		</div>
	</div>

	<div class="card" style="margin-top:1rem; padding:0">
		<table>
			<thead>
				<tr>
					<th>{t('dash.col.node')}</th>
					<th class="r">{t('dash.routing.col.routes')}</th>
					<th>{t('dash.routing.col.v4v6')}</th>
					<th class="r">{t('dash.routing.col.rpki')}</th>
					<th>{t('routing.captured')}</th>
				</tr>
			</thead>
			<tbody>
				{#each data.nodes as n (n.node_id)}
					<tr>
						<td><a href="/nodes/{n.node_id}?tab=routing" class="mono">{n.node_id}</a></td>
						<td class="r">
							<div class="routes-cell">
								<span class="mono">{n.route_count}</span>
								<MiniBar value={n.route_count} max={maxRoutes} color={V4_COLOR} />
							</div>
						</td>
						<td class="mono faint">{n.route_count_v4} / {n.route_count_v6}</td>
						<td class="r mono">{n.rpki.valid}</td>
						<td class="faint">{relTime(n.captured_at)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<style>
	.head {
		margin-bottom: 0.75rem;
		align-items: baseline;
	}
	.head h2 {
		margin: 0;
		font-size: 1.1rem;
	}
	.routing-card {
		display: flex;
		align-items: center;
		gap: 2.5rem;
		flex-wrap: wrap;
	}
	.donut-wrap {
		display: flex;
		align-items: center;
		gap: 1rem;
	}
	.legend {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.82rem;
		color: var(--text-dim);
	}
	.legend span {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
	.sw {
		width: 11px;
		height: 11px;
		border-radius: 3px;
		display: inline-block;
	}
	th.r,
	td.r {
		text-align: right;
	}
	.routes-cell {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		justify-content: flex-end;
		min-width: 120px;
	}
</style>
