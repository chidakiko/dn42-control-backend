<script lang="ts">
	// Fleet-wide routing overview (Cloudflare Radar "routing stats" style): a stat
	// strip of big numbers + half-donut gauges for RPKI and address-family splits,
	// plus a per-node breakdown table.
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import type { FleetRouting } from '$lib/types';
	import Donut from '$lib/components/charts/Donut.svelte';
	import MiniBar from '$lib/components/charts/MiniBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
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

	const pct = (v: number, tot: number) => (tot > 0 ? Math.round((v / tot) * 100) : 0);
	let rpkiTotal = $derived(data ? data.summary.rpki.valid + data.summary.rpki.invalid + data.summary.rpki.not_found : 0);
	let famTotal = $derived(data ? data.summary.route_count_v4 + data.summary.route_count_v6 : 0);
	let validPct = $derived(data ? pct(data.summary.rpki.valid, rpkiTotal) : 0);
	let v4pct = $derived(data ? pct(data.summary.route_count_v4, famTotal) : 0);

	let maxRoutes = $derived(data ? Math.max(1, ...data.nodes.map((n) => n.route_count)) : 1);
	const fmt = (n: number) => n.toLocaleString();
</script>

<div class="page-head" style="margin-bottom:1rem">
	<div>
		<div class="ph-title">
			<Icon name="route" size={20} />
			<h2 style="margin:0; font-size:1.15rem">{t('dash.routing.title')}</h2>
		</div>
		<p class="ph-sub">{t('dash.routing.subtitle')}</p>
	</div>
	{#if data}<span class="faint">{t('dash.routing.reporting', data.summary.nodes_reporting)}</span>{/if}
</div>

{#if loading && !data}
	<div class="card"><div class="empty">{t('common.loading')}</div></div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if data && data.summary.nodes_reporting === 0}
	<div class="card"><div class="empty">{t('dash.routing.empty')}</div></div>
{:else if data}
	<div class="card stats-card">
		<div class="strip">
			<div class="stat">
				<span class="lbl">{t('routing.total')}</span>
				<span class="num">{fmt(data.summary.route_count)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.v4')}</span>
				<span class="num" style="color:{V4_COLOR}">{fmt(data.summary.route_count_v4)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.v6')}</span>
				<span class="num" style="color:{V6_COLOR}">{fmt(data.summary.route_count_v6)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.rpki.valid')}</span>
				<span class="num">{fmt(data.summary.rpki.valid)} <small>({pct(data.summary.rpki.valid, rpkiTotal)}%)</small></span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.rpki.invalid')}</span>
				<span class="num">{fmt(data.summary.rpki.invalid)} <small>({pct(data.summary.rpki.invalid, rpkiTotal)}%)</small></span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.rpki.not_found')}</span>
				<span class="num">{fmt(data.summary.rpki.not_found)} <small>({pct(data.summary.rpki.not_found, rpkiTotal)}%)</small></span>
			</div>
		</div>

		<div class="gauges">
			<div class="gauge">
				<div class="g-label">
					<span class="dot" style="background:var(--c-ok)"></span>{t('routing.rpki.valid')}
					<b>{validPct}%</b>
				</div>
				<Donut half segments={rpkiSegments} size={150} thickness={18} />
			</div>
			<div class="gauge">
				<div class="g-label">
					<span class="dot" style="background:{V4_COLOR}"></span>IPv4 <b>{v4pct}%</b>
					<span class="sep">·</span>IPv6 <b>{100 - v4pct}%</b>
				</div>
				<Donut half segments={familySegments} size={150} thickness={18} />
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
								<span class="mono">{fmt(n.route_count)}</span>
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
	.stats-card {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 2rem;
		flex-wrap: wrap;
	}
	/* stat strip: bordered cells of label + big number (Radar-style) */
	.strip {
		display: flex;
		flex-wrap: wrap;
		flex: 1;
		min-width: 280px;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		padding: 0.1rem 1.1rem;
		border-left: 1px solid var(--border);
	}
	.stat:first-child {
		border-left: none;
		padding-left: 0;
	}
	.stat .lbl {
		font-size: 0.72rem;
		color: var(--text-faint);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		white-space: nowrap;
	}
	.stat .num {
		font-size: 1.45rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		line-height: 1.1;
	}
	.stat .num small {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text-dim);
	}
	.gauges {
		display: flex;
		gap: 1.5rem;
		flex: none;
	}
	.gauge {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.3rem;
	}
	.g-label {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.8rem;
		color: var(--text-dim);
		white-space: nowrap;
	}
	.g-label b {
		color: var(--text);
		font-variant-numeric: tabular-nums;
	}
	.g-label .dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
	}
	.g-label .sep {
		color: var(--text-faint);
		margin: 0 0.1rem;
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
