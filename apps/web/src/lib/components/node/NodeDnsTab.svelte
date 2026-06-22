<script lang="ts">
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import type { DnsGroupOut } from '$lib/types';

	let {
		nodeId,
		currentGroupId,
		onchange
	}: { nodeId: string; currentGroupId: number | null; onchange?: () => void } = $props();

	let groups = $state<DnsGroupOut[]>([]);
	let selected = $state<number | ''>('');
	let loading = $state(true);
	let saving = $state(false);
	let error = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			groups = await api.listDnsGroups();
		} catch (e) {
			error = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		load();
	});
	// 父级重载节点后 currentGroupId 变化 → 同步下拉选中。
	$effect(() => {
		selected = currentGroupId ?? '';
	});

	let currentName = $derived(groups.find((g) => g.id === currentGroupId)?.name ?? null);

	async function apply() {
		saving = true;
		try {
			const gid = selected === '' ? null : Number(selected);
			await api.assignNodeDnsGroup(nodeId, gid);
			toast.success(t('node.dns.assigned'));
			onchange?.();
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			saving = false;
		}
	}
</script>

<div class="card">
	<h2 style="margin-top:0">{t('node.dns.heading')}</h2>
	<p class="faint" style="font-size:0.85rem">{t('node.dns.hint')}</p>
	{#if error}<p class="error-text">{error}</p>{/if}

	<div class="row">
		<span class="faint">{t('node.dns.current')}:</span>
		<span class="mono">{currentName ?? t('node.dns.none')}</span>
	</div>
	<div class="row">
		<select bind:value={selected} disabled={loading} style="width:auto; min-width:14rem">
			<option value="">{t('node.dns.none')}</option>
			{#each groups as g (g.id)}
				<option value={g.id}>{g.name}</option>
			{/each}
		</select>
		<button class="btn primary sm" onclick={apply} disabled={saving || loading}>
			{t('node.dns.apply')}
		</button>
		<a class="btn ghost sm" href="/dns-groups">{t('node.dns.manage')}</a>
	</div>
</div>

<style>
	.row {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin-top: 0.75rem;
	}
</style>
