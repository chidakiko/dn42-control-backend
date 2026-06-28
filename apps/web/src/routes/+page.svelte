<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import type { FleetOverview, TrafficPoint } from '$lib/types';
	import FleetRouting from '$lib/components/FleetRouting.svelte';
	import { fmtBytes } from '$lib/format';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import FleetMap from '$lib/components/FleetMap.svelte';
	import TrendChart from '$lib/components/charts/TrendChart.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { fade } from 'svelte/transition';

	// --- real fleet WG throughput (rx/tx bytes/s) from /fleet/traffic ---
	const RX_COLOR = '#2f6fed';
	const TX_COLOR = '#38bdf8';
	const fmtRate = (v: number) => fmtBytes(v) + '/s';

	let fleetTrafficPts = $state<TrafficPoint[]>([]);
	let hasTraffic = $derived(fleetTrafficPts.length > 1);
	let trafficSeries = $derived([
		{ label: t('traffic.rx'), color: RX_COLOR, values: fleetTrafficPts.map((p) => p.rx_bytes_per_sec), fill: true },
		{ label: t('traffic.tx'), color: TX_COLOR, values: fleetTrafficPts.map((p) => p.tx_bytes_per_sec) }
	]);
	let trafficStamps = $derived(fleetTrafficPts.map((p) => p.captured_at));
	let lastPt = $derived(fleetTrafficPts.at(-1));
	let peakRx = $derived(fleetTrafficPts.reduce((m, p) => Math.max(m, p.rx_bytes_per_sec), 0));
	let peakTx = $derived(fleetTrafficPts.reduce((m, p) => Math.max(m, p.tx_bytes_per_sec), 0));

	let data = $state<FleetOverview | null>(null);
	let loading = $state(true);
	let error = $state('');

	async function load() {
		if (!data) loading = true; // spinner only on first load; polls update silently
		error = '';
		try {
			data = await api.fleetOverview();
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
		try {
			fleetTrafficPts = (await api.fleetTraffic()).points;
		} catch {
			fleetTrafficPts = [];
		}
	}

	$effect(() => {
		autoRefresh.tick;
		untrack(() => load());
	});

	let total = $derived(data ? data.nodes.length : 0);
	let okCount = $derived(data ? (data.summary.ok ?? 0) : 0);
</script>

<div class="page-head">
	<div>
		<div class="ph-title">
			<Icon name="dashboard" size={22} />
			<h1>{t('dash.title')}</h1>
		</div>
		<p class="ph-sub">{t('dash.subtitle')}</p>
	</div>
	<div class="ph-actions">
		<button class="btn sm" onclick={load} disabled={loading}>
			<Icon name="refresh" size={15} />{t('common.refresh')}
		</button>
	</div>
</div>

{#if loading && !data}
	<!-- shape-matched first-load skeleton: combined map + node-list card, then traffic -->
	<div class="card">
		<div class="sk-fleet">
			<div class="sk-map"><Skeleton h="320px" /></div>
			<div class="sk-list">
				{#each Array(6) as _, i (i)}<Skeleton h="2.6rem" />{/each}
			</div>
		</div>
	</div>
	<div class="card" style="margin-top:1.25rem"><Skeleton h="240px" /></div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if data}
	{#if data.nodes.length === 0}
		<div class="card" style="padding:0">
			<EmptyState icon="nodes" title={t('dash.empty')} hint={t('dash.subtitle')} />
		</div>
	{:else}
		<div class="page-head topo-head" in:fade={{ duration: 150 }}>
			<div>
				<div class="ph-title">
					<Icon name="nodes" size={20} />
					<h2 style="margin:0; font-size:1.15rem">{t('dash.topology')}</h2>
				</div>
				<p class="ph-sub">{t('dash.topologySub')}</p>
			</div>
			<span class="kpi"><strong>{okCount}/{total}</strong> {t('health.ok')}</span>
		</div>
		<div class="card" in:fade={{ duration: 150 }}>
			<FleetMap nodes={data.nodes} links={data.links} />
		</div>
	{/if}

	<div class="doc-routing" style="margin-top:1.75rem">
		<FleetRouting />
	</div>

	<div class="card doc-traffic" style="margin-top:1.25rem" in:fade={{ duration: 150 }}>
		<div class="card-head trend-head">
			<div>
				<div class="ph-title">
					<Icon name="activity" size={20} />
					<h2 style="margin:0; font-size:1.15rem">{t('dash.traffic')}</h2>
				</div>
				<p class="ph-sub">{t('dash.trafficSub')}</p>
			</div>
			{#if hasTraffic}
				<div class="trend-now">
					<span class="tn">
						<span class="ld" style="background:{RX_COLOR}"></span>{t('traffic.rx')}
						<b>{fmtRate(lastPt?.rx_bytes_per_sec ?? 0)}</b>
						<small>{t('traffic.peak')} {fmtRate(peakRx)}</small>
					</span>
					<span class="tn">
						<span class="ld" style="background:{TX_COLOR}"></span>{t('traffic.tx')}
						<b>{fmtRate(lastPt?.tx_bytes_per_sec ?? 0)}</b>
						<small>{t('traffic.peak')} {fmtRate(peakTx)}</small>
					</span>
				</div>
			{/if}
		</div>
		{#if hasTraffic}
			<TrendChart series={trafficSeries} timestamps={trafficStamps} height={240} zeroBased format={fmtRate} />
		{:else}
			<EmptyState icon="activity" title={t('dash.traffic.empty')} hint={t('dash.traffic.emptyHint')} />
		{/if}
	</div>
{/if}

<style>
	.kpi {
		font-size: 0.85rem;
		color: var(--text-dim);
	}
	.topo-head {
		margin-bottom: 1rem;
		align-items: flex-start;
	}
	.trend-head {
		align-items: flex-start;
	}
	.trend-head .ph-sub {
		margin: 0.15rem 0 0;
	}
	.trend-now {
		display: flex;
		flex-wrap: wrap;
		gap: 1.1rem;
		font-size: 0.8rem;
		color: var(--text-dim);
	}
	.tn {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
	.tn .ld {
		width: 16px;
		height: 3px;
		border-radius: 2px;
		display: inline-block;
	}
	.tn b {
		color: var(--text);
		font-variant-numeric: tabular-nums;
	}
	.tn small {
		color: var(--text-faint);
	}
	.kpi strong {
		font-variant-numeric: tabular-nums;
		font-size: 1.05rem;
		color: var(--ok);
		margin-right: 0.15rem;
	}
	/* first-load skeleton mirrors the combined map(60%) + list(40%) layout */
	.sk-fleet {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.sk-list {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	@media (min-width: 900px) {
		.sk-fleet {
			flex-direction: row;
		}
		.sk-map {
			flex: 0 0 60%;
		}
		.sk-list {
			flex: 0 0 calc(40% - 1rem);
		}
	}
</style>
