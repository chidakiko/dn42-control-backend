<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { fmtTime } from '$lib/format';
	import { t, locale } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { StatusEvent, DriftItem } from '$lib/types';
	import HealthBadge from './../HealthBadge.svelte';
	import Modal from './../Modal.svelte';
	import JsonView from './../JsonView.svelte';
	import DriftCards from './../DriftCards.svelte';
	import Select from './../Select.svelte';
	import SkeletonTable from './../SkeletonTable.svelte';
	import BarChart from './../charts/BarChart.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';

	let { nodeId }: { nodeId: string } = $props();

	let items = $state<StatusEvent[]>([]);
	let loading = $state(true);
	let err = $state('');
	let kind = $state('');
	let range = $state<'24h' | '7d' | '30d' | 'all'>('24h');

	let showPayload = $state(false);
	let payload = $state<unknown>(null);

	const RANGES = ['24h', '7d', '30d', 'all'] as const;
	const WINDOW: Record<string, number> = {
		'24h': 24 * 3600e3,
		'7d': 7 * 86400e3,
		'30d': 30 * 86400e3,
		all: Infinity
	};

	async function refresh() {
		if (items.length === 0) loading = true;
		err = '';
		try {
			const r = await api.statusEvents(nodeId, kind || undefined, 300);
			items = r.events;
		} catch (e) {
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		autoRefresh.tick;
		nodeId; // reload when the node changes
		untrack(() => refresh());
	});

	function toMs(s: string | null): number {
		if (!s) return NaN;
		const hasZone = /[zZ]$/.test(s) || /[+-]\d\d:?\d\d$/.test(s);
		return new Date(hasZone ? s : s + 'Z').getTime();
	}

	function statusKey(status: string | null): 'ok' | 'bad' | 'other' {
		if (!status) return 'other';
		if (['succeeded', 'ok', 'running'].includes(status)) return 'ok';
		if (['failed', 'degraded', 'bad'].includes(status)) return 'bad';
		return 'other';
	}

	// events filtered to the chosen window (table + chart share this)
	let windowed = $derived.by(() => {
		const now = Date.now();
		const w = WINDOW[range];
		if (w === Infinity) return items;
		return items.filter((e) => {
			const ms = toMs(e.created_at);
			return !isNaN(ms) && now - ms <= w;
		});
	});

	const COLORS = { ok: 'var(--c-ok)', bad: 'var(--c-bad)', other: 'var(--c-unknown)' };
	const B = 24; // buckets

	let buckets = $derived.by(() => {
		const ts = windowed.map((e) => toMs(e.created_at)).filter((n) => !isNaN(n));
		if (ts.length === 0) return [];
		const max = Math.max(...ts);
		const min = range === 'all' ? Math.min(...ts) : max - WINDOW[range];
		const span = Math.max(1, max - min);
		const size = span / B;
		const groups = Array.from({ length: B }, (_, i) => ({
			label: '',
			parts: { ok: 0, bad: 0, other: 0 } as Record<string, number>,
			at: min + i * size
		}));
		for (const e of windowed) {
			const ms = toMs(e.created_at);
			if (isNaN(ms)) continue;
			let idx = Math.floor((ms - min) / size);
			if (idx < 0) idx = 0;
			if (idx >= B) idx = B - 1;
			groups[idx].parts[statusKey(e.status)] += 1;
		}
		const dayMode = range === '30d' || range === 'all';
		return groups.map((g) => ({
			label: new Date(g.at).toLocaleString(locale.tag, dayMode
				? { month: 'numeric', day: 'numeric' }
				: { hour: '2-digit', minute: '2-digit' }),
			parts: [
				{ key: 'ok', value: g.parts.ok, color: COLORS.ok },
				{ key: 'bad', value: g.parts.bad, color: COLORS.bad },
				{ key: 'other', value: g.parts.other, color: COLORS.other }
			]
		}));
	});

	let hasChart = $derived(buckets.some((g) => g.parts.some((p) => p.value > 0)));

	function view(ev: StatusEvent) {
		payload = ev.payload;
		showPayload = true;
	}

	// A report payload carries payload.drift[]; surface it as cards, JSON as fallback.
	let driftItems = $derived.by(() => {
		const d = (payload as { drift?: unknown })?.drift;
		return Array.isArray(d) ? (d as DriftItem[]) : [];
	});
</script>

<div class="card-head">
	<h3>{t('status.title')} <span class="faint">({windowed.length})</span></h3>
	<div class="inline">
		<div class="seg">
			{#each RANGES as r (r)}
				<button class="segbtn" class:active={range === r} onclick={() => (range = r)}>
					{t(`range.${r}`)}
				</button>
			{/each}
		</div>
		<Select
			width="auto"
			value={kind}
			options={[
				{ value: '', label: t('status.allKinds') },
				{ value: 'snapshot', label: 'snapshot' },
				{ value: 'report', label: 'report' },
				{ value: 'apply', label: 'apply' }
			]}
			onChange={(v) => {
				kind = v;
				refresh();
			}}
		/>
		<button class="btn sm icon" onclick={refresh} disabled={loading} aria-label={t('common.refresh')}><Icon name="refresh" size={15} /></button>
	</div>
</div>

{#if loading && items.length === 0}
	<SkeletonTable
		headers={[t('status.col.kind'), t('status.col.gen'), t('status.col.status'), t('status.col.when'), '']}
		cols={['5rem', '3rem', '4rem', '7rem', '2rem']}
	/>
{:else if err}
	<p class="error-text">{err}</p>
{:else if items.length === 0}
	<div class="empty">{t('status.empty')}</div>
{:else}
	{#if hasChart}
		<div class="timeline">
			<div class="tl-head">
				<span class="faint">{t('status.timeline')}</span>
				<div class="key">
					<span><span class="kd" style="background:var(--c-ok)"></span>{t('st.succeeded')}</span>
					<span><span class="kd" style="background:var(--c-bad)"></span>{t('st.failed')}</span>
				</div>
			</div>
			<BarChart groups={buckets} height={110} />
		</div>
	{/if}

	<table>
		<thead>
			<tr><th>{t('status.col.kind')}</th><th>{t('status.col.gen')}</th><th>{t('status.col.status')}</th><th>{t('status.col.when')}</th><th></th></tr>
		</thead>
		<tbody>
			{#each windowed as ev (ev.id)}
				<tr>
					<td><span class="tag">{ev.kind}</span></td>
					<td class="mono">{ev.generation ?? '—'}</td>
					<td><HealthBadge value={ev.status} /></td>
					<td class="faint">{fmtTime(ev.created_at)}</td>
					<td class="actions">
						<button class="btn ghost sm" onclick={() => view(ev)}>{t('status.payload')}</button>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<Modal title={t('status.payloadTitle')} bind:open={showPayload}>
	{#if driftItems.length}
		<DriftCards items={driftItems} />
		<details class="rawjson">
			<summary>{t('drift.rawJson')}</summary>
			<JsonView value={payload} max />
		</details>
	{:else}
		<JsonView value={payload} max />
	{/if}
</Modal>

<style>
	.seg {
		display: inline-flex;
		border: 1px solid var(--border-strong);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}
	.segbtn {
		background: var(--bg-elev);
		border: none;
		border-right: 1px solid var(--border);
		color: var(--text-dim);
		padding: 0.25rem 0.55rem;
		font: inherit;
		font-size: 0.78rem;
		cursor: pointer;
	}
	.segbtn:last-child {
		border-right: none;
	}
	.segbtn.active {
		background: var(--accent);
		color: #fff;
	}
	.timeline {
		padding: 0.5rem 0.25rem 0.75rem;
		margin-bottom: 0.5rem;
	}
	.tl-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 0.5rem;
		font-size: 0.75rem;
	}
	.key {
		display: flex;
		gap: 0.85rem;
		color: var(--text-dim);
	}
	.key span {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.kd {
		width: 9px;
		height: 9px;
		border-radius: 2px;
		display: inline-block;
	}
	.rawjson {
		margin-top: 0.75rem;
		border-top: 1px solid var(--border);
		padding-top: 0.6rem;
	}
	.rawjson > summary {
		cursor: pointer;
		font-size: 0.8rem;
		color: var(--text-dim);
		user-select: none;
	}
	.rawjson > summary:hover {
		color: var(--text);
	}
	.rawjson > :global(.json-view),
	.rawjson > :global(pre) {
		margin-top: 0.5rem;
	}
</style>
