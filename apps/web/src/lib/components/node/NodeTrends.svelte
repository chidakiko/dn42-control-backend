<script lang="ts">
	// Radar-style mini stat cards for a node's overview: current drift (with
	// trend + delta vs the previous report) and apply success rate, each with a
	// sparkline derived from the recent report/apply event history.
	import { api } from '$lib/api';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { t } from '$lib/i18n.svelte';
	import Sparkline from '../charts/Sparkline.svelte';
	import HealthBadge from '../HealthBadge.svelte';
	import Skeleton from '../Skeleton.svelte';
	import { fade } from 'svelte/transition';
	import type { StatusEvent } from '$lib/types';

	let { nodeId }: { nodeId: string } = $props();

	let reports = $state<StatusEvent[]>([]);
	let applies = $state<StatusEvent[]>([]);
	let loaded = $state(false);

	async function load() {
		try {
			const [r, a] = await Promise.all([
				api.statusEvents(nodeId, 'report', 50),
				api.statusEvents(nodeId, 'apply', 50)
			]);
			reports = r.events;
			applies = a.events;
		} catch {
			/* overview is best-effort */
		} finally {
			loaded = true;
		}
	}
	$effect(() => {
		autoRefresh.tick;
		load();
	});

	function driftOf(e: StatusEvent): number {
		const d = (e.payload as { drift?: unknown[] })?.drift;
		return Array.isArray(d) ? d.length : 0;
	}

	// events arrive newest-first; reverse for chronological sparklines
	let driftSeries = $derived([...reports].reverse().map(driftOf));
	let curDrift = $derived(driftSeries.length ? driftSeries[driftSeries.length - 1] : 0);
	let prevDrift = $derived(driftSeries.length > 1 ? driftSeries[driftSeries.length - 2] : curDrift);
	let driftDelta = $derived(curDrift - prevDrift);

	let applySeries = $derived([...applies].reverse().map((e) => (e.status === 'succeeded' ? 1 : 0)));
	let succ = $derived(applies.filter((e) => e.status === 'succeeded').length);
	let rate = $derived(applies.length ? Math.round((succ / applies.length) * 100) : 0);
	let lastApply = $derived(applies.length ? applies[0].status : null);

	let hasData = $derived(reports.length > 0 || applies.length > 0);
</script>

{#if !loaded}
	<div class="trends">
		{#each Array(3) as _, i (i)}
			<div class="mini">
				<Skeleton w="3rem" h="0.66rem" />
				<Skeleton w="3.5rem" h="1.5rem" />
				{#if i < 2}<Skeleton h="30px" />{/if}
			</div>
		{/each}
	</div>
{:else if hasData}
	<div class="trends" in:fade={{ duration: 150 }}>
		<div class="mini">
			<span class="lbl">{t('trends.drift')}</span>
			<div class="row1">
				<span class="val">{curDrift}</span>
				{#if driftDelta !== 0}
					<span class="delta {driftDelta > 0 ? 'bad' : 'good'}">
						{driftDelta > 0 ? '▲' : '▼'}{Math.abs(driftDelta)}
					</span>
				{/if}
			</div>
			{#if driftSeries.length > 1}
				<Sparkline
					values={driftSeries}
					width={150}
					height={30}
					color={curDrift > 0 ? 'var(--c-bad)' : 'var(--c-ok)'}
				/>
			{/if}
		</div>

		<div class="mini">
			<span class="lbl">{t('trends.applyRate')}</span>
			<div class="row1">
				<span class="val">{rate}<span class="pct">%</span></span>
				<span class="sub faint">{succ}/{applies.length}</span>
			</div>
			{#if applySeries.length > 1}
				<Sparkline values={applySeries} width={150} height={30} color="var(--c-ok)" />
			{/if}
		</div>

		<div class="mini">
			<span class="lbl">{t('trends.lastApply')}</span>
			<div class="row1">
				<HealthBadge value={lastApply} />
			</div>
		</div>
	</div>
{/if}

<style>
	.trends {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
		gap: 1rem;
		margin-bottom: 1.25rem;
	}
	.mini {
		background: var(--bg-elev-2);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 0.7rem 0.85rem;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.lbl {
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-faint);
	}
	.row1 {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
	}
	.val {
		font-size: 1.7rem;
		font-weight: 700;
		line-height: 1;
		font-variant-numeric: tabular-nums;
	}
	.pct {
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--text-dim);
	}
	.delta {
		font-size: 0.8rem;
		font-weight: 600;
	}
	.delta.bad {
		color: var(--bad);
	}
	.delta.good {
		color: var(--ok);
	}
	.sub {
		font-size: 0.85rem;
	}
</style>
