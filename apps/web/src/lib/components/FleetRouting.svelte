<script lang="ts">
	// Fleet-wide routing overview. Because iBGP converges every node onto ~the same
	// RIB, plotting per-node route counts is redundant (overlapping lines). Instead:
	//   left  — the fleet route-table SIZE trend (one line; nodes are converged so the
	//           median represents the fleet) + route CHANGE (churn: announced/withdrawn).
	//   right — a node list surfacing what actually differs per node: convergence status
	//           (does its count match the fleet? a lagging node = iBGP/convergence issue)
	//           and RPKI-invalid counts (differ by each node's external peers).
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import type { FleetRoutingOverview } from '$lib/types';
	import TrendChart from '$lib/components/charts/TrendChart.svelte';
	import BarChart from '$lib/components/charts/BarChart.svelte';
	import Donut from '$lib/components/charts/Donut.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t, locale } from '$lib/i18n.svelte';

	let data = $state<FleetRoutingOverview | null>(null);
	let loading = $state(true);
	let error = $state('');

	const V4_COLOR = 'var(--c-accent)';
	const V6_COLOR = 'var(--c-ok)';

	async function load() {
		if (!data) loading = true;
		error = '';
		try {
			// One BFF call: summary + per-node + server-aggregated size/churn trend.
			data = await api.routingFleetOverview();
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

	const pct = (v: number, tot: number) => (tot > 0 ? Math.round((v / tot) * 100) : 0);
	const fmt = (n: number) => n.toLocaleString();
	function median(a: number[]): number {
		if (!a.length) return 0;
		const s = [...a].sort((x, y) => x - y);
		const m = Math.floor(s.length / 2);
		return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
	}

	let rpkiTotal = $derived(
		data ? data.summary.rpki.valid + data.summary.rpki.invalid + data.summary.rpki.not_found : 0
	);
	let invalidTotal = $derived(data ? data.summary.rpki.invalid : 0);

	// fleet address-family split donut (IPv4 / IPv6)
	let famTotal = $derived(data ? data.summary.route_count_v4 + data.summary.route_count_v6 : 0);
	let v4pct = $derived(data ? pct(data.summary.route_count_v4, famTotal) : 0);
	let familySegments = $derived(
		data
			? [
					{ label: t('routing.v4'), value: data.summary.route_count_v4, color: V4_COLOR },
					{ label: t('routing.v6'), value: data.summary.route_count_v6, color: V6_COLOR }
				]
			: []
	);

	// fleet RPKI distribution donut (valid / not-found / invalid)
	let rpkiSegments = $derived(
		data
			? [
					{ label: t('routing.rpki.valid'), value: data.summary.rpki.valid, color: 'var(--c-ok)' },
					{ label: t('routing.rpki.not_found'), value: data.summary.rpki.not_found, color: 'var(--c-warn)' },
					{ label: t('routing.rpki.invalid'), value: data.summary.rpki.invalid, color: 'var(--c-bad)' }
				]
			: []
	);

	// per-node RPKI composition (this is what genuinely differs node-to-node — each
	// node's external peers send different valid/not-found/invalid mixes).
	function rpki(n: { rpki: { valid: number; invalid: number; not_found: number } }) {
		const tot = n.rpki.valid + n.rpki.not_found + n.rpki.invalid;
		return {
			tot,
			validPct: pct(n.rpki.valid, tot),
			vw: tot ? (n.rpki.valid / tot) * 100 : 0,
			nw: tot ? (n.rpki.not_found / tot) * 100 : 0,
			iw: tot ? (n.rpki.invalid / tot) * 100 : 0
		};
	}

	// --- fleet trend (size + churn): server-aggregated 5-min buckets from the BFF ---
	let fleetTrend = $derived.by(() => {
		const tr = data?.trend ?? [];
		return {
			stamps: tr.map((p) => p.captured_at ?? ''),
			size: tr.map((p) => p.size),
			ann: tr.map((p) => p.announced),
			wd: tr.map((p) => p.withdrawn)
		};
	});
	let hasTrend = $derived(fleetTrend.size.length > 1);
	let totalChurn = $derived(
		fleetTrend.ann.reduce((s, v) => s + v, 0) + fleetTrend.wd.reduce((s, v) => s + v, 0)
	);

	let sizeSeries = $derived([
		{ label: t('routing.sizeTrend'), color: V4_COLOR, values: fleetTrend.size, fill: true }
	]);
	function hhmm(iso: string): string {
		return new Date(iso).toLocaleTimeString(locale.tag, { hour: '2-digit', minute: '2-digit' });
	}
	let churnGroups = $derived(
		fleetTrend.stamps.map((s, i) => ({
			label: hhmm(s),
			parts: [
				{ key: 'announced', value: fleetTrend.ann[i], color: 'var(--c-ok)' },
				{ key: 'withdrawn', value: fleetTrend.wd[i], color: 'var(--c-bad)' }
			]
		}))
	);

	// --- per-node convergence: a node whose count lags the fleet median = problem ---
	let fleetMedian = $derived(data ? median(data.nodes.map((n) => n.route_count)) : 0);
	function behind(n: { route_count: number }): number {
		return fleetMedian - n.route_count;
	}
	function diverged(n: { route_count: number }): boolean {
		return fleetMedian > 0 && Math.abs(behind(n)) > Math.max(20, fleetMedian * 0.02);
	}
	let divergedNodes = $derived(data ? data.nodes.filter(diverged) : []);

	// --- fleet RIB top origin-AS table (the dimension that actually varies row-to-row;
	// per-node route counts are identical once iBGP converges) ---
	let origins = $derived(data?.origins ?? []);
	let maxOrigin = $derived(origins.length ? origins[0].count : 0);
</script>

<div class="page-head" style="margin-bottom:1rem">
	<div>
		<div class="ph-title">
			<Icon name="route" size={20} />
			<h2 style="margin:0; font-size:1.15rem">{t('dash.routing.title')}</h2>
		</div>
		<p class="ph-sub">{t('dash.routing.subtitle')}</p>
	</div>
	{#if data}<span class="faint">{t('dash.routing.reporting', data.summary.nodes_reporting)}</span>
	{:else if loading}<Skeleton w="84px" h="0.9rem" />{/if}
</div>

{#if loading && !data}
	<!-- shape-matched skeleton: same cards + static headers as loaded, only data slots
	     are skeletonized so the section frame doesn't shift on load. -->
	<div class="card head-card">
		<div class="strip">
			{#each Array(6) as _, i (i)}
				<div class="stat">
					<Skeleton w="44px" h="0.7rem" />
					<Skeleton w="58px" h="1.4rem" />
				</div>
			{/each}
		</div>
		<div class="donuts">
			<div class="donut-block"><Skeleton circle h="108px" /></div>
			<div class="donut-block"><Skeleton circle h="108px" /></div>
		</div>
	</div>
	<div class="rt-2col">
		<div class="card rt-chart">
			<div class="card-head"><h3>{t('routing.sizeTrend')}</h3></div>
			<Skeleton h="170px" />
			<div class="card-head" style="margin-top:0.6rem"><h3 class="muted">{t('routing.churn')}</h3></div>
			<Skeleton h="90px" />
		</div>
		<div class="card rt-list">
			<div class="card-head">
				<h3>{t('routing.topOrigins')}</h3>
				<span class="faint" style="font-size:0.78rem">{t('routing.topOriginsSub')}</span>
			</div>
			<div class="org-list">
				{#each Array(10) as _, i (i)}<Skeleton h="1.4rem" />{/each}
			</div>
		</div>
	</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if data && data.summary.nodes_reporting === 0}
	<div class="card"><div class="empty">{t('dash.routing.empty')}</div></div>
{:else if data}
	<!-- headline numbers + fleet RPKI donut -->
	<div class="card head-card">
		<div class="strip">
			<div class="stat">
				<span class="lbl">{t('routing.total')}</span><span class="num">{fmt(data.summary.route_count)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.v4')}</span><span class="num" style="color:var(--c-accent)">{fmt(data.summary.route_count_v4)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.v6')}</span><span class="num" style="color:var(--c-ok)">{fmt(data.summary.route_count_v6)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.rpki.valid')}</span><span class="num">{pct(data.summary.rpki.valid, rpkiTotal)}<small>%</small></span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.rpki.invalid')}</span>
				<span class="num" class:bad-num={invalidTotal > 0}>{fmt(data.summary.rpki.invalid)}</span>
			</div>
			<div class="stat">
				<span class="lbl">{t('routing.rpki.not_found')}</span><span class="num">{fmt(data.summary.rpki.not_found)}</span>
			</div>
		</div>
		<div class="donuts">
			{#if famTotal > 0}
				<div class="donut-block">
					<Donut
						segments={familySegments}
						size={108}
						thickness={14}
						centerValue="{v4pct}%"
						centerLabel={t('routing.v4')}
					/>
					<div class="legend">
						<span><i style="background:{V4_COLOR}"></i>{t('routing.v4')} {fmt(data.summary.route_count_v4)}</span>
						<span><i style="background:{V6_COLOR}"></i>{t('routing.v6')} {fmt(data.summary.route_count_v6)}</span>
					</div>
				</div>
			{/if}
			{#if rpkiTotal > 0}
				<div class="donut-block">
					<Donut
						segments={rpkiSegments}
						size={108}
						thickness={14}
						centerValue="{pct(data.summary.rpki.valid, rpkiTotal)}%"
						centerLabel={t('routing.rpki.center')}
					/>
					<div class="legend">
						<span><i style="background:var(--c-ok)"></i>{t('routing.rpki.valid')}</span>
						<span><i style="background:var(--c-warn)"></i>{t('routing.rpki.not_found')}</span>
						<span><i style="background:var(--c-bad)"></i>{t('routing.rpki.invalid')}</span>
					</div>
				</div>
			{/if}
		</div>
	</div>

	{#if divergedNodes.length > 0}
		<div class="rt-alert">
			<Icon name="alert-triangle" size={15} />
			{t('dash.routing.divergeAlert', divergedNodes.length)}
		</div>
	{/if}
	{#if invalidTotal > 0}
		<div class="rt-alert">
			<Icon name="alert-triangle" size={15} />
			{t('dash.routing.invalidAlert', invalidTotal)}
		</div>
	{/if}

	<div class="rt-2col">
		<!-- left: fleet route-table size trend + churn -->
		<div class="card rt-chart">
			<div class="card-head"><h3>{t('routing.sizeTrend')}</h3></div>
			{#if hasTrend}
				<TrendChart
					series={sizeSeries}
					timestamps={fleetTrend.stamps}
					height={170}
					format={fmt}
					leftAxisWidth={48}
				/>
				<div class="card-head" style="margin-top:0.6rem">
					<h3 class="muted">{t('routing.churn')}</h3>
					<span class="faint" style="font-size:0.78rem">Σ {fmt(totalChurn)}</span>
				</div>
				<BarChart groups={churnGroups} height={90} leftAxisWidth={48} />
			{:else}
				<div class="empty">{t('common.loading')}</div>
			{/if}
		</div>

		<!-- right: fleet RIB top origin-AS table (per-node counts converge — origins are the
		     dimension that actually varies; counts aggregated fleet-wide, per-ASN max) -->
		<div class="card rt-list">
			<div class="card-head">
				<h3>{t('routing.topOrigins')}</h3>
				<span class="faint" style="font-size:0.78rem">{t('routing.topOriginsSub')}</span>
			</div>
			{#if origins.length}
				<div class="org-list">
					{#each origins as o, i (o.asn)}
						<div class="org-row">
							<span class="org-rank">{i + 1}</span>
							<span class="org-asn mono">AS{o.asn}</span>
							<span class="org-bar"><i style="width:{maxOrigin ? (o.count / maxOrigin) * 100 : 0}%"></i></span>
							<span class="org-count mono">{fmt(o.count)}</span>
						</div>
					{/each}
				</div>
			{:else}
				<div class="empty">{t('common.loading')}</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.head-card {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1.5rem;
		flex-wrap: wrap;
	}
	.strip {
		display: flex;
		flex-wrap: wrap;
		flex: 1;
		min-width: 0;
	}
	.donuts {
		display: flex;
		align-items: center;
		gap: 1.6rem;
		flex-wrap: wrap;
	}
	.donut-block {
		display: flex;
		align-items: center;
		gap: 0.85rem;
	}
	.donut-block .legend {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.74rem;
		color: var(--text-dim);
	}
	.donut-block .legend span {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		white-space: nowrap;
	}
	.donut-block .legend i {
		width: 9px;
		height: 9px;
		border-radius: 2px;
		flex: 0 0 auto;
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
	.stat .num.bad-num {
		color: var(--c-bad);
	}

	.rt-alert {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-top: 1rem;
		padding: 0.55rem 0.8rem;
		font-size: 0.82rem;
		font-weight: 500;
		color: var(--c-bad);
		background: color-mix(in srgb, var(--c-bad) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--c-bad) 30%, transparent);
		border-radius: var(--radius);
	}

	.rt-2col {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		margin-top: 1rem;
	}
	/* the two side-by-side cards are adjacent .card siblings, so the global
	   `.card + .card { margin-top }` rule pushes the right one down — cancel it
	   so both card headers sit on the same row. */
	.rt-2col > .card + .card {
		margin-top: 0;
	}
	@media (min-width: 900px) {
		.rt-2col {
			flex-direction: row;
			align-items: stretch;
		}
		.rt-chart {
			flex: 0 0 60%;
			min-width: 0;
		}
		.rt-list {
			flex: 0 0 calc(40% - 1rem);
			display: flex;
			flex-direction: column;
			min-height: 0;
		}
	}

	/* fleet RIB top origin-AS list (horizontal bars). Only this list scrolls — the
	   card header (Top origin AS · subtitle) stays fixed above it. */
	.org-list {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		margin-top: 0.4rem;
		flex: 1;
		min-height: 0;
		overflow-y: auto;
	}
	.org-row {
		display: grid;
		grid-template-columns: 16px 96px 1fr 48px;
		align-items: center;
		column-gap: 0.55rem;
		padding: 0.32rem 0.4rem;
		border-radius: var(--radius-sm);
	}
	.org-row:hover {
		background: var(--bg-elev-2);
	}
	.org-rank {
		font-size: 0.7rem;
		color: var(--text-faint);
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.org-asn {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.org-bar {
		height: 7px;
		border-radius: 4px;
		background: var(--bg-elev-2);
		overflow: hidden;
	}
	.org-bar i {
		display: block;
		height: 100%;
		min-width: 2px;
		border-radius: 4px;
		background: var(--c-accent);
	}
	.org-count {
		font-size: 0.78rem;
		color: var(--text-dim);
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
</style>
