<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import type {
		RouteEntry,
		RoutingOrigins,
		RoutingPrefixes,
		RoutingSummary,
		RoutingTimeline
	} from '$lib/types';
	import Donut from './../charts/Donut.svelte';
	import BarChart from './../charts/BarChart.svelte';
	import TrendChart from './../charts/TrendChart.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Modal from './../Modal.svelte';

	let { nodeId }: { nodeId: string } = $props();

	let summary = $state<RoutingSummary | null>(null);
	let origins = $state<RoutingOrigins | null>(null);
	let timeline = $state<RoutingTimeline | null>(null);
	let prefixes = $state<RoutingPrefixes | null>(null);
	let loading = $state(true);
	let err = $state('');

	// prefixes table controls
	let family = $state<'' | '4' | '6'>('');
	let scope = $state<'all' | 'local' | 'external'>('all');
	let q = $state('');
	let offset = $state(0);
	const LIMIT = 100;

	const RPKI_COLORS: Record<string, string> = {
		valid: 'var(--c-ok)',
		invalid: 'var(--c-bad)',
		not_found: 'var(--c-warn)'
	};
	const V4_COLOR = 'var(--c-accent)';
	const V6_COLOR = 'var(--c-ok)';

	async function loadHead() {
		if (!summary) loading = true;
		err = '';
		try {
			const [s, o, tl] = await Promise.all([
				api.routingSummary(nodeId),
				api.routingOrigins(nodeId, 15),
				api.routingTimeline(nodeId, 200)
			]);
			summary = s;
			origins = o;
			timeline = tl;
		} catch (e) {
			// 404 = never reported → show empty state, not an error banner.
			summary = null;
			origins = null;
			timeline = null;
			err = '';
		} finally {
			loading = false;
		}
	}

	async function loadPrefixes() {
		try {
			prefixes = await api.routingPrefixes(nodeId, {
				family: family || undefined,
				scope,
				q: q.trim() || undefined,
				limit: LIMIT,
				offset
			});
		} catch (e) {
			err = errorMessage(e);
		}
	}

	// Depend only on the refresh tick + node id; untrack the loader so its
	// internal reads (e.g. `summary`, which it also writes) don't make this
	// effect self-trigger into a request storm.
	$effect(() => {
		autoRefresh.tick;
		nodeId;
		untrack(() => loadHead());
	});

	// Reload the prefixes page whenever a filter/search/page changes (or once
	// summary first arrives). untrack the loader for the same reason.
	$effect(() => {
		family;
		scope;
		q;
		offset;
		const ready = summary !== null;
		if (ready) untrack(() => loadPrefixes());
	});

	function resetAndSearch() {
		offset = 0;
	}

	// --- chart data ---
	let familySegments = $derived(
		summary
			? [
					{ label: t('routing.v4'), value: summary.route_count_v4, color: V4_COLOR },
					{ label: t('routing.v6'), value: summary.route_count_v6, color: V6_COLOR }
				]
			: []
	);

	let rpkiSegments = $derived(
		summary
			? (['valid', 'invalid', 'not_found'] as const).map((k) => ({
					label: t(`routing.rpki.${k}`),
					value: summary!.rpki[k],
					color: RPKI_COLORS[k]
				}))
			: []
	);

	// RPKI 甜甜圈中心：有效率%。
	let rpkiValidPct = $derived.by(() => {
		const r = summary?.rpki;
		if (!r) return '';
		const tot = r.valid + r.invalid + r.not_found;
		return tot > 0 ? Math.round((r.valid / tot) * 100) + '%' : '';
	});

	// ROA 表整张没采到 ⇒ RPKI 校验不可用,显式提示(不再悄悄塞「未知」)。
	let rpkiUnavailable = $derived(summary != null && summary.rpki_observed === false);

	// 过滤前(import-table);旧 agent / 采集失败为 null。
	let prefilter = $derived(summary?.prefilter ?? null);
	// 过滤结果完整分解(4 段,和≈收到):接受·有效 / 接受·未覆盖 / 拒绝·无效 / 拒绝·其他。
	let prefilterOutcome = $derived.by(() => {
		if (!prefilter) return [];
		const { received, accepted, invalid, not_found } = prefilter;
		const acceptNotFound = Math.min(not_found, accepted);
		const acceptValid = Math.max(0, accepted - acceptNotFound);
		const rejectOther = Math.max(0, received - accepted - invalid);
		return [
			{ label: t('routing.prefilter.acceptValid'), value: acceptValid, color: 'var(--c-ok)' },
			{ label: t('routing.prefilter.acceptNotFound'), value: acceptNotFound, color: 'var(--c-warn)' },
			{ label: t('routing.prefilter.rejectInvalid'), value: invalid, color: 'var(--c-bad)' },
			{ label: t('routing.prefilter.rejectOther'), value: rejectOther, color: 'var(--c-unknown)' }
		];
	});
	let prefilterRejectedPct = $derived.by(() => {
		if (!prefilter || prefilter.received === 0) return '';
		return Math.round(((prefilter.received - prefilter.accepted) / prefilter.received) * 100) + '%';
	});
	// per-peer:被拒(invalid+not_found 占主)最多的在前。默认折叠显示 4 条。
	let prefilterPeers = $derived(
		prefilter
			? [...prefilter.peers]
					.map((p) => ({ ...p, rejected: p.received - p.accepted }))
					.sort((a, b) => b.rejected - a.rejected || b.received - a.received)
			: []
	);
	let peersExpanded = $state(false);
	let shownPeers = $derived(peersExpanded ? prefilterPeers : prefilterPeers.slice(0, 4));
	let invalidRoutes = $derived(prefilter?.invalid_routes ?? []);
	let filteredRoutes = $derived(prefilter?.filtered_routes ?? []);
	// 被拒原因枚举 → 本地化文案；未知值回退原串。
	function reasonLabel(reason: string | null | undefined): string {
		if (!reason) return '—';
		const key = `routing.prefilter.reason.${reason}`;
		const label = t(key);
		return label === key ? reason : label;
	}
	// 被拒明细：按原因筛选 + 分页（默认 15 条/页）。
	const FILTERED_PAGE = 15;
	const REASON_ORDER = ['out_of_range', 'self_net', 'as_path_too_long', 'blocked_asn', 'policy'];
	let filterReason = $state('');
	let filteredPage = $state(0);
	let reasonCounts = $derived.by(() => {
		const m: Record<string, number> = {};
		for (const r of filteredRoutes) {
			const k = r.reason ?? 'policy';
			m[k] = (m[k] ?? 0) + 1;
		}
		return m;
	});
	let reasonOptions = $derived(REASON_ORDER.filter((k) => (reasonCounts[k] ?? 0) > 0));
	let filteredView = $derived(
		filterReason
			? filteredRoutes.filter((r) => (r.reason ?? 'policy') === filterReason)
			: filteredRoutes
	);
	let filteredPages = $derived(Math.max(1, Math.ceil(filteredView.length / FILTERED_PAGE)));
	let filteredSafePage = $derived(Math.min(filteredPage, filteredPages - 1));
	let filteredPageRows = $derived(
		filteredView.slice(filteredSafePage * FILTERED_PAGE, filteredSafePage * FILTERED_PAGE + FILTERED_PAGE)
	);

	// 起源 AS Top 榜 / 按 peer 统计路由：默认 top 6,其余折叠。
	let originsExpanded = $state(false);
	let allOrigins = $derived(origins?.origins ?? []);
	let shownOrigins = $derived(originsExpanded ? allOrigins : allOrigins.slice(0, 6));
	let routePeersExpanded = $state(false);
	let allRoutePeers = $derived(summary?.peers ?? []);
	let shownRoutePeers = $derived(routePeersExpanded ? allRoutePeers : allRoutePeers.slice(0, 6));

	// prefix-length distribution: one bar per length, stacked v4 + v6.
	let prefixLenGroups = $derived.by(() => {
		if (!summary) return [];
		const v4 = summary.prefix_lengths['4'] ?? {};
		const v6 = summary.prefix_lengths['6'] ?? {};
		const lengths = [...new Set([...Object.keys(v4), ...Object.keys(v6)])]
			.map(Number)
			.sort((a, b) => a - b);
		return lengths.map((len) => ({
			label: `/${len}`,
			parts: [
				{ key: 'v4', value: v4[len] ?? 0, color: V4_COLOR },
				{ key: 'v6', value: v6[len] ?? 0, color: V6_COLOR }
			]
		}));
	});

	let asPathGroups = $derived.by(() => {
		if (!summary) return [];
		return Object.keys(summary.as_path_lengths)
			.map(Number)
			.sort((a, b) => a - b)
			.map((len) => ({
				label: String(len),
				parts: [
					{ key: 'n', value: summary!.as_path_lengths[len], color: V4_COLOR }
				]
			}));
	});

	// 路由表规模趋势(Radar 风):单条总路由折线 + 渐变面积,Y 轴紧贴数据(波动可见),
	// X 轴真实时间戳。v4/v6 占比由上方甜甜圈呈现,这里专注总规模随时间的走势。
	let routeCountSeries = $derived(timeline ? timeline.events.map((e) => e.route_count) : []);
	let timelineStamps = $derived(timeline ? timeline.events.map((e) => e.captured_at) : []);
	let timelineSeries = $derived(
		timeline
			? [{ label: t('routing.total'), color: 'var(--c-accent)', fill: true, values: routeCountSeries }]
			: []
	);

	let churnGroups = $derived(
		timeline
			? timeline.events.map((e) => ({
					label: '',
					parts: [
						{ key: 'announced', value: e.announced, color: V6_COLOR },
						{ key: 'withdrawn', value: e.withdrawn, color: 'var(--c-bad)' }
					]
				}))
			: []
	);

	let hasChurn = $derived(churnGroups.some((g) => g.parts.some((p) => p.value > 0)));

	function rpkiClass(v: string | null): string {
		if (v === 'valid') return 'ok';
		if (v === 'invalid') return 'bad';
		if (v === 'not-found') return 'warn';
		return 'muted';
	}

	// How many routes share each prefix on the current page (multipath grouping).
	let prefixCounts = $derived.by(() => {
		const m = new Map<string, number>();
		for (const r of prefixes?.routes ?? []) m.set(r.prefix, (m.get(r.prefix) ?? 0) + 1);
		return m;
	});
	function groupSize(prefix: string): number {
		return prefixCounts.get(prefix) ?? 1;
	}

	// Route detail modal: shows every path for the clicked prefix (full AS path +
	// communities), best highlighted.
	let showDetail = $state(false);
	let detailPrefix = $state('');
	let detailRoutes = $derived<RouteEntry[]>(
		prefixes ? prefixes.routes.filter((r) => r.prefix === detailPrefix) : []
	);
	function openDetail(prefix: string) {
		detailPrefix = prefix;
		showDetail = true;
	}
</script>

<div class="card-head">
	<h3>{t('routing.title')}</h3>
	<button class="btn sm icon" onclick={loadHead} disabled={loading} aria-label={t('common.refresh')}><Icon name="refresh" size={15} /></button>
</div>

{#if loading && !summary}
	<div class="empty">{t('common.loading')}</div>
{:else if !summary}
	<div class="empty">{t('routing.empty')}</div>
{:else}
	{#if summary.observation === 'unavailable'}
		<div class="note warn">{t('routing.unavailableNote')}</div>
	{/if}

	<!-- headline stats -->
	<div class="stats">
		<div class="stat">
			<span class="num mono">{summary.route_count}</span>
			<span class="lbl">{t('routing.totalRoutes')}</span>
		</div>
		<div class="stat">
			<span class="num mono">{summary.route_count_v4}</span>
			<span class="lbl">{t('routing.v4')}</span>
		</div>
		<div class="stat">
			<span class="num mono">{summary.route_count_v6}</span>
			<span class="lbl">{t('routing.v6')}</span>
		</div>
		<div class="stat">
			<span class="num mono">{summary.local_count}</span>
			<span class="lbl">{t('routing.localOrigin')}</span>
		</div>
		<div class="stat">
			<span class="num mono faint">{fmtTime(summary.captured_at)}</span>
			<span class="lbl">{t('routing.captured')}</span>
		</div>
	</div>

	{#if rpkiUnavailable}
		<p class="roa-warn">⚠ {t('routing.rpki.unavailable')}</p>
	{/if}

	<!-- donuts: family + rpki -->
	<div class="donuts">
		<div class="donut-card">
			<Donut segments={familySegments} size={140} thickness={20} centerValue={summary.route_count} centerLabel={t('routing.routes')} />
			<div class="legend">
				{#each familySegments as s (s.label)}
					<span><span class="kd" style="background:{s.color}"></span>{s.label} <b>{s.value}</b></span>
				{/each}
			</div>
		</div>
		<div class="donut-card">
			<Donut segments={rpkiSegments} size={140} thickness={20} centerValue={rpkiValidPct} centerLabel={t('routing.rpki.center')} />
			<div class="legend">
				{#each rpkiSegments as s (s.label)}
					<span><span class="kd" style="background:{s.color}"></span>{s.label} <b>{s.value}</b></span>
				{/each}
			</div>
		</div>
		{#if prefilter}
			<div class="donut-card">
				<Donut segments={prefilterOutcome} size={140} thickness={20} centerValue={prefilterRejectedPct} centerLabel={t('routing.prefilter.center')} />
				<div class="legend">
					{#each prefilterOutcome as s (s.label)}
						<span><span class="kd" style="background:{s.color}"></span>{s.label} <b>{s.value}</b></span>
					{/each}
				</div>
			</div>
		{/if}
	</div>

	<!-- distributions -->
	<div class="grid2">
		<div class="chart-block">
			<span class="faint">{t('routing.prefixLen')}</span>
			<BarChart groups={prefixLenGroups} height={120} maxLabels={12} />
		</div>
		<div class="chart-block">
			<span class="faint">{t('routing.asPathLen')}</span>
			<BarChart groups={asPathGroups} height={120} maxLabels={12} />
		</div>
	</div>

	<!-- timeline (Radar 风趋势图) -->
	{#if routeCountSeries.length > 1}
		<div class="chart-block trend-block">
			<div class="trend-head">
				<div>
					<h4>{t('routing.timeline')}</h4>
					<span class="faint sub">{t('routing.timelineSub')}</span>
				</div>
				{#if timelineSeries.length > 1}
					<div class="trend-legend">
						{#each timelineSeries as s (s.label)}
							<span><span class="ld" style="background:{s.color}"></span>{s.label}</span>
						{/each}
					</div>
				{/if}
			</div>
			<TrendChart
				series={timelineSeries}
				timestamps={timelineStamps}
				height={200}
				format={(v) => Math.round(v).toLocaleString()}
			/>
		</div>
	{/if}
	{#if hasChurn}
		<div class="chart-block">
			<span class="faint">{t('routing.churn')}</span>
			<BarChart groups={churnGroups} height={80} maxLabels={8} />
			<div class="legend">
				<span><span class="kd" style="background:{V6_COLOR}"></span>{t('routing.announced')}</span>
				<span><span class="kd" style="background:var(--c-bad)"></span>{t('routing.withdrawn')}</span>
			</div>
		</div>
	{/if}

	<!-- 过滤前(import-table)：各对端 + 无效路由 -->
	{#if prefilter}
		<p class="faint hint">{t('routing.prefilter.note')}</p>
		{#if prefilterPeers.length}
			<div class="chart-block">
				<span class="faint">{t('routing.prefilter.byPeer')}</span>
				<table class="pf">
					<thead>
						<tr>
							<th>{t('routing.prefilter.col.peer')}</th>
							<th class="r">{t('routing.prefilter.col.recv')}</th>
							<th class="r">{t('routing.prefilter.col.acc')}</th>
							<th class="r">{t('routing.prefilter.col.rej')}</th>
							<th class="r">{t('routing.rpki.invalid')}</th>
							<th class="r">{t('routing.rpki.not_found')}</th>
						</tr>
					</thead>
					<tbody>
						{#each shownPeers as p (p.protocol)}
							<tr class:bad={p.rejected > 0}>
								<td class="mono">{p.protocol}{p.remote_asn ? ` · AS${p.remote_asn}` : ''}</td>
								<td class="r mono">{p.received}</td>
								<td class="r mono">{p.accepted}</td>
								<td class="r mono">{p.rejected || ''}</td>
								<td class="r mono">{p.invalid || ''}</td>
								<td class="r mono">{p.not_found || ''}</td>
							</tr>
						{/each}
					</tbody>
				</table>
				{#if prefilterPeers.length > 4}
					<button class="btn ghost sm" onclick={() => (peersExpanded = !peersExpanded)}>
						{peersExpanded
							? t('routing.prefilter.collapse')
							: t('routing.prefilter.expand', prefilterPeers.length - 4)}
					</button>
				{/if}
			</div>
		{/if}
		{#if invalidRoutes.length}
			<div class="chart-block">
				<span class="faint">{t('routing.prefilter.invalidTitle')} <b>({invalidRoutes.length})</b></span>
				<table class="pf">
					<thead>
						<tr>
							<th>{t('routing.col.prefix')}</th>
							<th>{t('routing.prefilter.col.originAsn')}</th>
							<th>{t('routing.prefilter.col.peer')}</th>
						</tr>
					</thead>
					<tbody>
						{#each invalidRoutes as r, i (`${r.prefix}-${r.protocol}-${i}`)}
							<tr class="bad">
								<td class="mono">{r.prefix}</td>
								<td class="mono">{r.origin_asn ? `AS${r.origin_asn}` : '—'}</td>
								<td class="mono faint">{r.protocol}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
		{#if filteredRoutes.length}
			<div class="chart-block">
				<div class="card-head">
					<span class="faint"
						>{t('routing.prefilter.filteredTitle')}
						<b>({filteredRoutes.length}{filteredRoutes.length >= 5000 ? '+' : ''})</b></span
					>
					<select bind:value={filterReason} onchange={() => (filteredPage = 0)} style="width:auto">
						<option value=""
							>{t('routing.prefilter.reason.col')}: {t('common.total')} ({filteredRoutes.length})</option
						>
						{#each reasonOptions as k (k)}
							<option value={k}>{reasonLabel(k)} ({reasonCounts[k]})</option>
						{/each}
					</select>
				</div>
				<table class="pf">
					<thead>
						<tr>
							<th>{t('routing.col.prefix')}</th>
							<th>{t('routing.prefilter.col.originAsn')}</th>
							<th>{t('routing.prefilter.reason.col')}</th>
							<th>{t('routing.prefilter.col.peer')}</th>
						</tr>
					</thead>
					<tbody>
						{#each filteredPageRows as r (`${r.prefix}-${r.protocol}`)}
							<tr>
								<td class="mono">{r.prefix}</td>
								<td class="mono">{r.origin_asn ? `AS${r.origin_asn}` : '—'}</td>
								<td
									><span
										class="reason"
										class:bad={r.reason === 'out_of_range' || r.reason === 'blocked_asn'}
										class:muted={r.reason === 'policy'}>{reasonLabel(r.reason)}</span
									></td
								>
								<td class="mono faint">{r.protocol}</td>
							</tr>
						{/each}
					</tbody>
				</table>
				{#if filteredPages > 1}
					<div class="pager">
						<button
							class="btn ghost sm"
							disabled={filteredSafePage === 0}
							onclick={() => (filteredPage = filteredSafePage - 1)}>‹</button
						>
						<span class="faint">{filteredSafePage + 1} / {filteredPages} · {filteredView.length}</span>
						<button
							class="btn ghost sm"
							disabled={filteredSafePage >= filteredPages - 1}
							onclick={() => (filteredPage = filteredSafePage + 1)}>›</button
						>
					</div>
				{/if}
			</div>
		{/if}
	{/if}

	<!-- origins + peers -->
	<div class="grid2">
		<div>
			<h4>{t('routing.origins')}</h4>
			<table>
				<thead><tr><th>{t('routing.col.asn')}</th><th class="r">{t('routing.col.count')}</th></tr></thead>
				<tbody>
					{#each shownOrigins as o (o.asn)}
						<tr><td class="mono">AS{o.asn}</td><td class="r mono">{o.count}</td></tr>
					{/each}
				</tbody>
			</table>
			{#if allOrigins.length > 6}
				<button class="btn ghost sm" onclick={() => (originsExpanded = !originsExpanded)}>
					{originsExpanded ? t('routing.collapse') : t('routing.showMore', allOrigins.length - 6)}
				</button>
			{/if}
		</div>
		<div>
			<h4>{t('routing.peers')}</h4>
			<table>
				<thead><tr><th>{t('routing.col.peer')}</th><th class="r">{t('routing.col.count')}</th></tr></thead>
				<tbody>
					{#each shownRoutePeers as p (p.protocol)}
						<tr><td class="mono">{p.protocol}</td><td class="r mono">{p.count}</td></tr>
					{/each}
				</tbody>
			</table>
			{#if allRoutePeers.length > 6}
				<button class="btn ghost sm" onclick={() => (routePeersExpanded = !routePeersExpanded)}>
					{routePeersExpanded ? t('routing.collapse') : t('routing.showMore', allRoutePeers.length - 6)}
				</button>
			{/if}
		</div>
	</div>

	<!-- prefixes table -->
	<div class="card-head" style="margin-top:1rem">
		<h4>{t('routing.prefixes')} {#if prefixes}<span class="faint">({prefixes.total})</span>{/if}</h4>
		<div class="inline">
			<select bind:value={family} onchange={resetAndSearch} style="width:auto">
				<option value="">{t('routing.family')}: {t('common.total')}</option>
				<option value="4">{t('routing.v4')}</option>
				<option value="6">{t('routing.v6')}</option>
			</select>
			<select bind:value={scope} onchange={resetAndSearch} style="width:auto">
				<option value="all">{t('routing.scope')}: {t('routing.scope.all')}</option>
				<option value="local">{t('routing.scope.local')}</option>
				<option value="external">{t('routing.scope.external')}</option>
			</select>
			<input
				placeholder={t('routing.search')}
				bind:value={q}
				onkeydown={(e) => e.key === 'Enter' && resetAndSearch()}
				style="width:auto"
			/>
			<button class="btn sm" onclick={resetAndSearch}>{t('common.refresh')}</button>
		</div>
	</div>

	{#if prefixes}
		<table>
			<thead>
				<tr>
					<th>{t('routing.col.prefix')}</th>
					<th>{t('routing.col.origin')}</th>
					<th>{t('routing.col.path')}</th>
					<th>{t('routing.col.nexthop')}</th>
					<th>{t('routing.col.proto')}</th>
					<th>{t('routing.col.rpki')}</th>
				</tr>
			</thead>
			<tbody>
				{#each prefixes.routes as r (r.prefix + (r.protocol ?? '') + (r.next_hop ?? ''))}
					{@const multi = groupSize(r.prefix) > 1}
					<tr
						class="clickable"
						class:best={multi && r.primary}
						class:backup={multi && !r.primary}
						onclick={() => openDetail(r.prefix)}
					>
						<td class="mono">
							{r.prefix}
							{#if r.local}<span class="local-tag" title={t('routing.localOrigin')}>{t('routing.local')}</span>{/if}
							{#if multi && r.primary}<span class="best-tag">{t('routing.best')}</span>{/if}
							{#if multi && !r.primary}<span class="backup-tag">{t('routing.backup')}</span>{/if}
						</td>
						<td class="mono">{r.origin_asn ? `AS${r.origin_asn}` : '—'}</td>
						<td class="mono faint path">
							{#if r.as_path.length === 0}—{:else}{r.as_path.slice(0, 2).join(' ')}{#if r.as_path.length > 2}<span class="morep">{t('routing.morePath', r.as_path.length - 2)}</span>{/if}{/if}
						</td>
						<td class="mono faint">{r.next_hop ?? '—'}</td>
						<td class="mono faint">{r.protocol ?? '—'}</td>
						<td>{#if r.rpki}<span class="rpki {rpkiClass(r.rpki)}">{r.rpki}</span>{:else}—{/if}</td>
					</tr>
				{/each}
			</tbody>
		</table>
		<div class="pager">
			<span class="faint">{t('routing.showing', prefixes.routes.length, prefixes.total)}</span>
			<div class="inline">
				<button class="btn ghost sm" disabled={offset === 0} onclick={() => (offset = Math.max(0, offset - LIMIT))}>{t('routing.prev')}</button>
				<button class="btn ghost sm" disabled={offset + LIMIT >= prefixes.total} onclick={() => (offset += LIMIT)}>{t('routing.next')}</button>
			</div>
		</div>
	{/if}
{/if}

<Modal title="{t('routing.detailTitle')} · {detailPrefix}" bind:open={showDetail}>
	<div class="detail">
		<div class="detail-sub faint">{t('routing.paths', detailRoutes.length)}</div>
		{#each detailRoutes as r, i (i)}
			<div class="path-card" class:best={r.primary}>
				<div class="path-head">
					{#if r.primary}<span class="best-tag">{t('routing.best')}</span>{:else}<span class="backup-tag">{t('routing.backup')}</span>{/if}
					{#if r.local}<span class="local-tag">{t('routing.local')}</span>{/if}
					{#if r.rpki}<span class="rpki {rpkiClass(r.rpki)}">{r.rpki}</span>{/if}
				</div>
				<dl>
					<dt>{t('routing.f.proto')}</dt>
					<dd class="mono">{r.protocol ?? '—'}</dd>
					<dt>{t('routing.f.nexthop')}</dt>
					<dd class="mono">{r.next_hop ?? '—'}</dd>
					<dt>{t('routing.f.aspath')}</dt>
					<dd class="mono wrap">{r.as_path.length ? r.as_path.join(' ') : '—'}</dd>
					<dt>{t('routing.f.communities')}</dt>
					<dd class="mono wrap">
						{#if (r.communities ?? []).length}{#each r.communities as c (c)}<span class="comm">{c}</span>{/each}{:else}—{/if}
					</dd>
					<dt>{t('routing.f.largeCommunities')}</dt>
					<dd class="mono wrap">
						{#if (r.large_communities ?? []).length}{#each r.large_communities as c (c)}<span class="comm lg">{c}</span>{/each}{:else}—{/if}
					</dd>
				</dl>
			</div>
		{/each}
	</div>
</Modal>

<style>
	.note {
		padding: 0.5rem 0.75rem;
		border-radius: var(--radius-sm);
		font-size: 0.82rem;
		margin-bottom: 0.75rem;
	}
	.note.warn {
		background: var(--warn-bg);
		color: var(--warn);
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
		gap: 1rem;
		margin-bottom: 1rem;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.stat .num {
		font-size: 1.5rem;
		font-weight: 700;
	}
	.stat .num.faint {
		font-size: 0.9rem;
		font-weight: 500;
	}
	.stat .lbl {
		color: var(--text-faint);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.donuts {
		display: flex;
		gap: 2rem;
		flex-wrap: wrap;
		margin-bottom: 1.25rem;
	}
	.donut-card {
		display: flex;
		align-items: center;
		gap: 1rem;
	}
	.legend {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.8rem;
		color: var(--text-dim);
	}
	.legend span {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
	.kd {
		width: 10px;
		height: 10px;
		border-radius: 2px;
		display: inline-block;
	}
	.grid2 {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
		gap: 1.5rem;
		margin-bottom: 1rem;
	}
	.chart-block {
		margin-bottom: 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	/* 趋势图(Radar 风):标题/副标题 + 图例,图表自身负责坐标轴 */
	.trend-block {
		gap: 0.2rem;
	}
	.trend-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		flex-wrap: wrap;
	}
	.trend-head h4 {
		margin: 0;
	}
	.trend-head .sub {
		font-size: 0.76rem;
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
	.ld {
		width: 14px;
		height: 3px;
		border-radius: 2px;
		display: inline-block;
	}
	h4 {
		margin: 0 0 0.5rem;
		font-size: 0.9rem;
	}
	th.r,
	td.r {
		text-align: right;
	}
	.hint {
		font-size: 0.78rem;
		margin: 0.1rem 0 0.5rem;
	}
	.roa-warn {
		margin: 0 0 0.8rem;
		padding: 0.5rem 0.8rem;
		border-radius: var(--radius-sm);
		font-size: 0.85rem;
		background: var(--warn-bg);
		color: var(--warn);
		border: 1px solid color-mix(in srgb, var(--warn) 40%, transparent);
	}
	.pf {
		width: 100%;
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}
	.pf tr.bad td.r {
		color: var(--bad);
		font-weight: 600;
	}
	.path {
		max-width: 220px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.rpki {
		font-size: 0.72rem;
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
	}
	.rpki.ok {
		background: var(--ok-bg);
		color: var(--ok);
	}
	.rpki.bad {
		background: var(--bad-bg);
		color: var(--bad);
	}
	.rpki.warn {
		background: var(--warn-bg);
		color: var(--warn);
	}
	.rpki.muted {
		color: var(--text-faint);
	}
	.reason {
		font-size: 0.7rem;
		padding: 0.08rem 0.4rem;
		border-radius: 3px;
		white-space: nowrap;
		background: var(--warn-bg);
		color: var(--warn);
	}
	.reason.bad {
		background: var(--bad-bg);
		color: var(--bad);
	}
	.reason.muted {
		background: transparent;
		color: var(--text-faint);
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.6rem;
		margin-top: 0.4rem;
		font-size: 0.78rem;
		font-variant-numeric: tabular-nums;
	}
	.local-tag {
		font-size: 0.66rem;
		padding: 0.05rem 0.35rem;
		margin-left: 0.4rem;
		border-radius: 3px;
		background: var(--accent-soft);
		color: var(--accent);
		border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
		vertical-align: middle;
	}
	tr.clickable {
		cursor: pointer;
	}
	tr.clickable:hover {
		background: var(--bg-elev-2);
	}
	/* best path within a multipath group: highlighted; backups dimmed. */
	tr.best {
		background: color-mix(in srgb, var(--c-ok) 9%, transparent);
	}
	tr.backup {
		opacity: 0.55;
	}
	.best-tag,
	.backup-tag {
		font-size: 0.66rem;
		padding: 0.05rem 0.35rem;
		margin-left: 0.4rem;
		border-radius: 3px;
		vertical-align: middle;
	}
	.best-tag {
		background: var(--ok-bg);
		color: var(--ok);
		border: 1px solid color-mix(in srgb, var(--ok) 35%, transparent);
	}
	.backup-tag {
		color: var(--text-faint);
		border: 1px solid var(--border);
	}
	.morep {
		margin-left: 0.35rem;
		font-size: 0.7rem;
		color: var(--text-faint);
	}
	.detail-sub {
		font-size: 0.8rem;
		margin-bottom: 0.6rem;
	}
	.path-card {
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 0.6rem 0.75rem;
		margin-bottom: 0.6rem;
	}
	.path-card.best {
		border-color: color-mix(in srgb, var(--ok) 45%, transparent);
		background: color-mix(in srgb, var(--c-ok) 7%, transparent);
	}
	.path-head {
		display: flex;
		gap: 0.4rem;
		align-items: center;
		margin-bottom: 0.5rem;
	}
	.path-card dl {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 0.3rem 0.9rem;
		margin: 0;
	}
	.path-card dt {
		color: var(--text-faint);
		font-size: 0.74rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.path-card dd {
		margin: 0;
		font-size: 0.85rem;
	}
	.wrap {
		word-break: break-word;
	}
	.comm {
		display: inline-block;
		padding: 0.05rem 0.35rem;
		margin: 0 0.3rem 0.3rem 0;
		border-radius: 3px;
		background: var(--bg-elev-2);
		font-size: 0.76rem;
	}
	.comm.lg {
		background: var(--accent-soft);
		color: var(--accent);
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-top: 0.6rem;
	}
</style>
