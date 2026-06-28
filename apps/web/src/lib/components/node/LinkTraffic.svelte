<script lang="ts">
	// Link traffic trend for a node. The server pre-computes the throughput series
	// (Δbytes / Δt from successive snapshots, counter resets clamped) and returns a
	// tiny points array — the browser no longer pulls 60 full snapshots and deltas
	// them. Feeds the shared TrendChart (the "traffic" template).
	import { untrack } from 'svelte';
	import { api } from '$lib/api';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t } from '$lib/i18n.svelte';
	import { fmtBytes } from '$lib/format';
	import TrendChart from '../charts/TrendChart.svelte';
	import type { TrafficPoint } from '$lib/types';

	let { nodeId }: { nodeId: string } = $props();

	let points = $state<TrafficPoint[]>([]);
	let loaded = $state(false);

	async function load() {
		try {
			const r = await api.nodeTraffic(nodeId, 120);
			points = r.points;
		} catch {
			/* best-effort overview widget */
		} finally {
			loaded = true;
		}
	}
	$effect(() => {
		autoRefresh.tick;
		nodeId; // reload when the node changes
		untrack(() => load());
	});

	let timestamps = $derived(points.map((p) => p.captured_at));
	let rxRate = $derived(points.map((p) => p.rx_bytes_per_sec));
	let txRate = $derived(points.map((p) => p.tx_bytes_per_sec));
	let hasData = $derived(points.length > 1);
	const fmtRate = (v: number) => fmtBytes(v) + '/s';
</script>

{#if loaded && hasData}
	<div class="traffic">
		<div class="th">
			<h4>{t('traffic.title')}</h4>
			<div class="legend">
				<span><span class="d rx"></span>{t('traffic.rx')}</span>
				<span><span class="d tx"></span>{t('traffic.tx')}</span>
			</div>
		</div>
		<TrendChart
			{timestamps}
			height={160}
			zeroBased
			format={fmtRate}
			series={[
				{ label: t('traffic.rx'), color: 'var(--ok)', values: rxRate, fill: true },
				{ label: t('traffic.tx'), color: 'var(--accent)', values: txRate }
			]}
		/>
	</div>
{/if}

<style>
	.traffic {
		margin-bottom: 1.25rem;
	}
	.th {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		margin-bottom: 0.4rem;
	}
	.th h4 {
		margin: 0;
		font-size: 0.95rem;
	}
	.legend {
		display: flex;
		gap: 0.85rem;
		font-size: 0.78rem;
		color: var(--text-dim);
	}
	.legend span {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.d {
		width: 9px;
		height: 9px;
		border-radius: 2px;
		display: inline-block;
	}
	.d.rx {
		background: var(--ok);
	}
	.d.tx {
		background: var(--accent);
	}
</style>
