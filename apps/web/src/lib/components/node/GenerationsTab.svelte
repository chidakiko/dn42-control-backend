<script lang="ts">
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { GenerationDiffOut, GenerationOut } from '$lib/types';
	import Modal from './../Modal.svelte';
	import SkeletonTable from './../SkeletonTable.svelte';
	import JsonView from './../JsonView.svelte';

	let { nodeId, onchange }: { nodeId: string; onchange?: () => void } = $props();

	let items = $state<GenerationOut[]>([]);
	let loading = $state(true);
	let err = $state('');

	let showSnap = $state(false);
	let snap = $state<unknown>(null);
	let snapGen = $state(0);

	let showDiff = $state(false);
	let diff = $state<GenerationDiffOut | null>(null);

	async function refresh() {
		loading = true;
		err = '';
		try {
			items = await api.listGenerations(nodeId, 100);
		} catch (e) {
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	onMount(refresh);

	async function viewSnapshot(gen: number) {
		try {
			const g = await api.getGeneration(nodeId, gen);
			snap = g.snapshot;
			snapGen = gen;
			showSnap = true;
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}

	async function viewDiff(gen: number) {
		try {
			diff = await api.diffGeneration(nodeId, gen);
			showDiff = true;
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}

	async function rollback(gen: number) {
		if (!confirm(t('gen.confirmRollback', gen))) return;
		try {
			await api.rollbackGeneration(nodeId, gen);
			toast.success(t('gen.rolledBack', gen));
			onchange?.();
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}
</script>

<div class="card-head">
	<h3>{t('gen.title')} <span class="faint">({items.length})</span></h3>
	<button class="btn sm icon" onclick={refresh} disabled={loading} aria-label={t('common.refresh')}><Icon name="refresh" size={15} /></button>
</div>

{#if loading && items.length === 0}
	<SkeletonTable
		headers={[t('gen.col.gen'), t('gen.col.reason'), t('gen.col.published'), '']}
		cols={['2.5rem', '12rem', '7rem', '4rem']}
	/>
{:else if err}
	<p class="error-text">{err}</p>
{:else if items.length === 0}
	<div class="empty">{t('gen.empty')}</div>
{:else}
	<table>
		<thead>
			<tr><th>{t('gen.col.gen')}</th><th>{t('gen.col.reason')}</th><th>{t('gen.col.published')}</th><th></th></tr>
		</thead>
		<tbody>
			{#each items as g (g.generation)}
				<tr>
					<td class="mono">{g.generation}</td>
					<td class="faint">{g.reason ?? '—'}</td>
					<td class="faint">{fmtTime(g.published_at)}</td>
					<td class="actions">
						<button class="btn ghost sm" onclick={() => viewSnapshot(g.generation)}>{t('gen.snapshot')}</button>
						{#if g.generation > 1}
							<button class="btn ghost sm" onclick={() => viewDiff(g.generation)}>{t('gen.diff')}</button>
						{/if}
						<button class="btn ghost sm" onclick={() => rollback(g.generation)}>{t('gen.rollback')}</button>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<Modal title={t('gen.snapshotTitle', snapGen)} bind:open={showSnap}>
	<JsonView value={snap} max />
</Modal>

<Modal title={t('gen.diffTitle')} bind:open={showDiff}>
	{#if diff}
		<p class="muted">
			{t(
				'gen.diffSummary',
				diff.from_generation,
				diff.to_generation,
				diff.changed ? t('gen.changes', diff.changes.length) : t('gen.noChanges')
			)}
		</p>
		{#if diff.changed}
			<JsonView value={diff.changes} max />
		{/if}
	{/if}
</Modal>
