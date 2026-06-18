<script lang="ts">
	import { untrack } from 'svelte';
	import { api, ApiError, errorMessage } from '$lib/api';
	import type { FleetHealth, NodeHealthValue } from '$lib/types';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import FleetRouting from '$lib/components/FleetRouting.svelte';
	import Donut from '$lib/components/charts/Donut.svelte';
	import MiniBar from '$lib/components/charts/MiniBar.svelte';
	import { relTime } from '$lib/format';
	import { toast } from '$lib/toast.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t } from '$lib/i18n.svelte';

	let data = $state<FleetHealth | null>(null);
	let loading = $state(true);
	let error = $state('');

	const ORDER: NodeHealthValue[] = ['ok', 'stale', 'degraded', 'down', 'unknown'];
	const COLOR: Record<NodeHealthValue, string> = {
		ok: 'var(--c-ok)',
		stale: 'var(--c-warn)',
		degraded: 'var(--c-bad)',
		down: 'var(--c-down)',
		unknown: 'var(--c-unknown)'
	};

	async function load() {
		if (!data) loading = true; // spinner only on first load; polls update silently
		error = '';
		try {
			data = await api.fleetHealth();
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
	let segments = $derived(
		data
			? ORDER.map((h) => ({ label: t(`health.${h}`), value: data!.summary[h] ?? 0, color: COLOR[h] }))
			: []
	);
	let maxDrift = $derived(data ? Math.max(1, ...data.nodes.map((n) => n.drift_count)) : 1);

	let filter = $state<NodeHealthValue | null>(null);
	let shown = $derived(
		data ? (filter ? data.nodes.filter((n) => n.health === filter) : data.nodes) : []
	);
	function toggle(h: NodeHealthValue) {
		filter = filter === h ? null : h;
	}
	function pct(v: number): string {
		return total > 0 ? `${Math.round((v / total) * 100)}%` : '0%';
	}
</script>

<div class="spread" style="margin-bottom:1.25rem">
	<h1>{t('dash.title')}</h1>
	<button class="btn sm" onclick={load} disabled={loading}>↻ {t('common.refresh')}</button>
</div>

{#if loading && !data}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if data}
	<div class="card health-card">
		<div class="donut-wrap">
			<Donut {segments} size={150} thickness={22} centerValue={total} centerLabel={t('nav.nodes')} />
		</div>
		<div class="legend">
			{#each ORDER as h (h)}
				<button
					class="leg"
					class:active={filter === h}
					class:dim={filter !== null && filter !== h}
					onclick={() => toggle(h)}
				>
					<span class="sw" style="background:{COLOR[h]}"></span>
					<span class="leg-label">{t(`health.${h}`)}</span>
					<span class="leg-num">{data.summary[h] ?? 0}</span>
					<span class="leg-pct faint">{pct(data.summary[h] ?? 0)}</span>
				</button>
			{/each}
			{#if filter}
				<button class="leg clear" onclick={() => (filter = null)}>✕ {t('common.total')}: {total}</button>
			{/if}
		</div>
	</div>

	<div class="card" style="margin-top:1.25rem; padding:0">
		{#if data.nodes.length === 0}
			<div class="empty">{t('dash.empty')}</div>
		{:else}
			<table>
				<thead>
					<tr>
						<th>{t('dash.col.node')}</th>
						<th>{t('dash.col.health')}</th>
						<th>{t('dash.col.genPair')}</th>
						<th>{t('dash.col.report')}</th>
						<th>{t('dash.col.apply')}</th>
						<th>{t('dash.col.drift')}</th>
						<th>{t('dash.col.lastSnapshot')}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each shown as n (n.node_id)}
						{@const outdated = n.health === 'down' || n.health === 'stale'}
						<tr>
							<td><a href="/nodes/{n.node_id}" class="mono">{n.node_id}</a></td>
							<td><HealthBadge value={n.health} /></td>
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
								/>
							</td>
							<td>
								<HealthBadge
									value={n.last_apply_status}
									muted={outdated}
									hint={outdated ? t('dash.lastKnown') : ''}
								/>
							</td>
							<td>
								<MiniBar
									value={n.drift_count}
									max={maxDrift}
									color={n.drift_count > 0 ? 'var(--c-bad)' : 'var(--c-unknown)'}
								/>
							</td>
							<td class="faint">{relTime(n.last_snapshot_at)}</td>
							<td class="actions">
								<button class="btn sm ghost" onclick={() => notify(n.node_id)}>
									{t('dash.requestSnapshot')}
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>

	<div style="margin-top:1.75rem">
		<FleetRouting />
	</div>
{/if}

<style>
	.health-card {
		display: flex;
		align-items: center;
		gap: 2.5rem;
		flex-wrap: wrap;
	}
	.donut-wrap {
		flex: none;
	}
	.legend {
		display: grid;
		grid-template-columns: 1fr;
		gap: 0.25rem;
		flex: 1;
		min-width: 220px;
		max-width: 360px;
	}
	.leg {
		display: grid;
		grid-template-columns: auto 1fr auto auto;
		align-items: center;
		gap: 0.65rem;
		padding: 0.4rem 0.6rem;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		cursor: pointer;
		font: inherit;
		color: var(--text);
		text-align: left;
		transition: background 0.12s, opacity 0.12s, border-color 0.12s;
	}
	.leg:hover {
		background: var(--bg-elev-2);
	}
	.leg.active {
		border-color: var(--accent);
		background: var(--accent-soft);
	}
	.leg.dim {
		opacity: 0.45;
	}
	.sw {
		width: 11px;
		height: 11px;
		border-radius: 3px;
	}
	.leg-label {
		font-weight: 500;
	}
	.leg-num {
		font-variant-numeric: tabular-nums;
		font-weight: 700;
		font-size: 1.05rem;
	}
	.leg-pct {
		font-size: 0.78rem;
		min-width: 2.6em;
		text-align: right;
	}
	.leg.clear {
		grid-template-columns: 1fr;
		color: var(--text-dim);
		font-size: 0.8rem;
		margin-top: 0.2rem;
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
