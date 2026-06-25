<script lang="ts">
	import { untrack } from 'svelte';
	import { api, ApiError, errorMessage } from '$lib/api';
	import type { FleetOverview } from '$lib/types';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import FleetRouting from '$lib/components/FleetRouting.svelte';
	import { relTime } from '$lib/format';
	import { toast } from '$lib/toast.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t } from '$lib/i18n.svelte';
	import Icon, { type IconName } from '$lib/components/Icon.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import FleetTopology from '$lib/components/FleetTopology.svelte';
	import TrendChart from '$lib/components/charts/TrendChart.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { fade } from 'svelte/transition';

	// --- demo traffic chart -------------------------------------------------
	// Sample (fake) traffic so the dashboard mirrors Cloudflare Radar's traffic
	// trend: two smooth solid lines (total / HTTP) plus dashed "previous period"
	// overlays, on a diurnal wave. Generated once at mount; deterministic noise
	// (no Math.random) so it doesn't jitter on refresh polls.
	const TRAFFIC_TOTAL = '#2f6fed';
	const TRAFFIC_HTTP = '#38bdf8';
	const fmtTB = (v: number) => `${v.toFixed(1)} TB`;

	function genTraffic() {
		const N = 84; // 7 days at 2h resolution
		const stepMs = 2 * 3600 * 1000;
		const start = Date.now() - (N - 1) * stepMs;
		const hash = (i: number) => {
			const x = Math.sin(i * 12.9898) * 43758.5453;
			return x - Math.floor(x); // 0..1
		};
		const stamps: string[] = [];
		const total: number[] = [];
		const http: number[] = [];
		const totalPrev: number[] = [];
		const httpPrev: number[] = [];
		for (let i = 0; i < N; i++) {
			const ts = start + i * stepMs;
			const d = new Date(ts);
			stamps.push(d.toISOString());
			const hod = d.getUTCHours() + d.getUTCMinutes() / 60;
			const diur = Math.sin(((hod - 6) / 24) * 2 * Math.PI); // -1..1, peak afternoon UTC
			const week = Math.sin((i / N) * Math.PI); // gentle hump across the window
			const tot = 17 + 6 * diur + 1.5 * week + (hash(i) - 0.5) * 1.6;
			total.push(tot);
			http.push(tot * 0.66 + (hash(i + 100) - 0.5) * 1.0);
			const totP = 16 + 5.6 * diur + 1.2 * week + (hash(i + 200) - 0.5) * 1.6;
			totalPrev.push(totP);
			httpPrev.push(totP * 0.64 + (hash(i + 300) - 0.5) * 1.0);
		}
		return { stamps, total, http, totalPrev, httpPrev };
	}
	const traffic = genTraffic();
	let trafficSeries = $derived([
		{ label: t('dash.traffic.total'), color: TRAFFIC_TOTAL, values: traffic.total },
		{ label: t('dash.traffic.http'), color: TRAFFIC_HTTP, values: traffic.http },
		{ label: `${t('dash.traffic.total')} · ${t('dash.traffic.prev')}`, color: TRAFFIC_TOTAL, values: traffic.totalPrev, dash: true },
		{ label: `${t('dash.traffic.http')} · ${t('dash.traffic.prev')}`, color: TRAFFIC_HTTP, values: traffic.httpPrev, dash: true }
	]);

	let data = $state<FleetOverview | null>(null);
	let loading = $state(true);
	let error = $state('');
	// Node ids visible in the map's current region view; null = show all.
	let visibleIds = $state<string[] | null>(null);

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
	}

	$effect(() => {
		autoRefresh.tick;
		untrack(() => load());
	});

	async function notify(nodeId: string) {
		try {
			const r = await api.notifyNode(nodeId, 'snapshot_request', 'dashboard refresh');
			toast.success(t('dash.snapshotRequested', r.delivered));
		} catch (err) {
			if (err instanceof ApiError && err.status === 409) {
				toast.error(t('disc.refused'));
				await load();
			} else {
				toast.error(errorMessage(err));
			}
		}
	}

	let total = $derived(data ? data.nodes.length : 0);
	let okCount = $derived(data ? (data.summary.ok ?? 0) : 0);

	// Node capabilities (from runtime service roles), shown as compact icons with a
	// hover label. Notable roles only; router-netns / debug-shell (every node) hidden.
	const CAP_META: Record<string, { label: string; icon: IconName }> = {
		dns: { label: 'DNS', icon: 'dns' },
		'rpki-cache': { label: 'RPKI', icon: 'shield-check' },
		'bird-router': { label: 'BIRD', icon: 'bird' },
		'wg-gateway': { label: 'WireGuard', icon: 'wireguard' }
	};
	const CAP_ORDER = ['dns', 'rpki-cache', 'bird-router', 'wg-gateway'];
	function capIcons(caps: string[] | undefined): { key: string; label: string; icon: IconName }[] {
		const set = new Set(caps ?? []);
		return CAP_ORDER.filter((c) => set.has(c)).map((c) => ({ key: c, ...CAP_META[c] }));
	}
	let shownNodes = $derived(
		data ? (visibleIds ? data.nodes.filter((n) => visibleIds!.includes(n.node_id)) : data.nodes) : []
	);
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
	<div class="card"><Skeleton h="300px" /></div>
	<div class="card" style="margin-top:1.25rem">
		<div class="stack" style="gap:0.85rem">
			{#each Array(6) as _, i (i)}<Skeleton h="1.3rem" />{/each}
		</div>
	</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if data}
	{#if data.nodes.length > 0}
		<div class="card doc-topology" in:fade={{ duration: 150 }}>
			<div class="card-head">
				<h2>{t('dash.topology')}</h2>
				<span class="kpi"><strong>{okCount}/{total}</strong> {t('health.ok')}</span>
			</div>
			<FleetTopology nodes={data.nodes} links={data.links} onview={(ids) => (visibleIds = ids)} />
		</div>
	{/if}

	<div class="card" style="margin-top:1.25rem; padding:0">
		{#if data.nodes.length === 0}
			<EmptyState icon="nodes" title={t('dash.empty')} hint={t('dash.subtitle')} />
		{:else}
			<table>
				<thead>
					<tr>
						<th>{t('dash.col.node')}</th>
						<th>{t('dash.col.health')}</th>
						<th>{t('dash.col.caps')}</th>
						<th>{t('dash.col.genPair')}</th>
						<th>{t('dash.col.report')}</th>
						<th>{t('dash.col.apply')}</th>
						<th>{t('dash.col.drift')}</th>
						<th>{t('dash.col.lastSnapshot')}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each shownNodes as n (n.node_id)}
						{@const outdated = n.health === 'down' || n.health === 'stale'}
						<tr>
							<td><a href="/nodes/{n.node_id}" class="mono">{n.node_id}</a></td>
							<td><HealthBadge value={n.health} /></td>
							<td>
								<div class="caps">
									{#each capIcons(n.capabilities) as c (c.key)}
										<span class="cap" title={c.label} aria-label={c.label}>
											<Icon name={c.icon} size={16} />
										</span>
									{:else}
										<span class="faint">—</span>
									{/each}
								</div>
							</td>
							<td class="mono genpair">
								{n.desired_generation ?? '—'}<span class="sep">/</span><span
									class:mismatch={!outdated &&
										n.observed_generation != null &&
										n.desired_generation != null &&
										n.observed_generation !== n.desired_generation}
									class:dim={outdated}
									title={outdated ? t('dash.lastKnown') : ''}
									>{n.observed_generation ?? '—'}</span
								>
							</td>
							<td>
								<HealthBadge
									value={n.last_report_status}
									muted={outdated}
									hint={outdated ? t('dash.lastKnown') : ''}
									compact
								/>
							</td>
							<td>
								<HealthBadge
									value={n.last_apply_status}
									muted={outdated}
									hint={outdated ? t('dash.lastKnown') : ''}
									compact
								/>
							</td>
							<td>
								{#if n.drift_count > 0}
									<span class="mono drift-num">{n.drift_count}</span>
								{:else}
									<span class="faint">—</span>
								{/if}
							</td>
							<td class="faint">{relTime(n.last_snapshot_at)}</td>
							<td class="actions">
								<button
									class="btn sm ghost icon"
									onclick={() => notify(n.node_id)}
									title={t('dash.requestSnapshot')}
									aria-label={t('dash.requestSnapshot')}
								>
									<Icon name="refresh" size={15} />
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>

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
			<div class="trend-legend">
				<span><span class="ld" style="background:{TRAFFIC_TOTAL}"></span>{t('dash.traffic.total')}</span>
				<span><span class="ld" style="background:{TRAFFIC_HTTP}"></span>{t('dash.traffic.http')}</span>
				<span><span class="ld dash"></span>{t('dash.traffic.prev')}</span>
			</div>
		</div>
		<TrendChart series={trafficSeries} timestamps={traffic.stamps} height={240} zeroBased format={fmtTB} />
	</div>
{/if}

<style>
	.kpi {
		font-size: 0.85rem;
		color: var(--text-dim);
	}
	.trend-head {
		align-items: flex-start;
	}
	.trend-head .ph-sub {
		margin: 0.15rem 0 0;
	}
	.trend-legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.9rem;
		font-size: 0.78rem;
		color: var(--text-dim);
	}
	.trend-legend span {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
	.trend-legend .ld {
		width: 16px;
		height: 3px;
		border-radius: 2px;
		display: inline-block;
	}
	.trend-legend .ld.dash {
		height: 0;
		border-top: 2px dashed var(--text-faint);
		border-radius: 0;
	}
	.kpi strong {
		font-variant-numeric: tabular-nums;
		font-size: 1.05rem;
		color: var(--ok);
		margin-right: 0.15rem;
	}
	.caps {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.cap {
		display: inline-flex;
		color: var(--text-dim);
		cursor: default;
	}
	.cap:hover {
		color: var(--accent);
	}
	.drift-num {
		color: var(--bad);
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.genpair .sep {
		color: var(--text-faint);
		margin: 0 0.35rem;
	}
	.genpair .mismatch {
		color: var(--warn);
		font-weight: 600;
	}
	.genpair .dim {
		opacity: 0.45;
	}
</style>
