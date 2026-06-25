<script lang="ts">
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import type { PeeringIn, PeeringOut } from '$lib/types';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Modal from './../Modal.svelte';

	let { nodeId, onchange }: { nodeId: string; onchange?: () => void } = $props();

	let items = $state<PeeringOut[]>([]);
	let loading = $state(true);
	let err = $state('');

	let showForm = $state(false);
	let editing = $state<PeeringOut | null>(null);
	let saving = $state(false);

	let f = $state({
		name: '',
		remote_asn: '',
		remote_node_id: '',
		remote_label: '',
		is_internal: false,
		enabled: true,
		notes: ''
	});

	async function refresh() {
		loading = true;
		err = '';
		try {
			items = await api.listPeerings(nodeId);
			onchange?.();
		} catch (e) {
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	onMount(refresh);

	function openEdit(p: PeeringOut) {
		editing = p;
		f = {
			name: p.name,
			remote_asn: String(p.remote_asn),
			remote_node_id: p.remote_node_id ?? '',
			remote_label: p.remote_label ?? '',
			is_internal: p.is_internal,
			enabled: p.enabled,
			notes: p.notes ?? ''
		};
		showForm = true;
	}

	async function save() {
		if (!f.name.trim() || !f.remote_asn.trim()) {
			toast.error(t('peer.req'));
			return;
		}
		const body: PeeringIn = {
			name: f.name.trim(),
			remote_asn: Number(f.remote_asn),
			remote_node_id: f.remote_node_id.trim() || null,
			remote_label: f.remote_label.trim() || null,
			is_internal: f.is_internal,
			enabled: f.enabled,
			notes: f.notes.trim() || null
		};
		saving = true;
		try {
			if (editing) {
				await api.updatePeering(editing.id, body);
				toast.success(t('peer.updated'));
			} else {
				await api.createPeering(nodeId, body);
				toast.success(t('peer.created'));
			}
			showForm = false;
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			saving = false;
		}
	}

	async function del(p: PeeringOut) {
		if (!confirm(t('peer.confirmDelete', p.name))) return;
		try {
			await api.deletePeering(p.id);
			toast.success(t('peer.deleted'));
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}
</script>

<div class="card-head">
	<h3>{t('peer.title')} <span class="faint">({items.length})</span></h3>
	<div class="inline">
		<button class="btn sm icon" onclick={refresh} disabled={loading} aria-label={t('common.refresh')}><Icon name="refresh" size={15} /></button>
	</div>
</div>
<p class="faint" style="font-size:0.78rem; margin-top:-0.4rem">{t('peer.note')}</p>

{#if loading && items.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if err}
	<p class="error-text">{err}</p>
{:else if items.length === 0}
	<div class="empty">{t('peer.empty')}</div>
{:else}
	<table>
		<thead>
			<tr>
				<th>{t('peer.col.name')}</th><th>{t('peer.col.remoteAsn')}</th><th>{t('peer.col.remoteNode')}</th><th>{t('peer.col.type')}</th><th>{t('peer.col.enabled')}</th><th></th>
			</tr>
		</thead>
		<tbody>
			{#each items as p (p.id)}
				<tr>
					<td class="mono">{p.name}</td>
					<td class="mono">AS{p.remote_asn}</td>
					<td class="mono faint">{p.remote_node_id ?? p.remote_label ?? '—'}</td>
					<td><span class="tag">{p.is_internal ? t('peer.internal') : t('peer.external')}</span></td>
					<td>
						<span class="badge {p.enabled ? 'ok' : 'unknown'}"
							><span class="dot"></span>{p.enabled ? t('common.on') : t('common.off2')}</span
						>
					</td>
					<td class="actions">
						<button class="btn ghost sm" onclick={() => openEdit(p)}>{t('common.edit')}</button>
						<button class="btn ghost sm danger" onclick={() => del(p)}>{t('common.delete')}</button>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<Modal title={editing ? t('peer.edit') : t('peer.add')} bind:open={showForm}>
	<div class="row">
		<label class="field"><span>{t('peer.f.name')} *</span><input bind:value={f.name} /></label>
		<label class="field"><span>{t('peer.f.remoteAsn')} *</span><input bind:value={f.remote_asn} /></label>
	</div>
	<div class="row">
		<label class="field"
			><span>{t('peer.f.remoteNode')}</span><input bind:value={f.remote_node_id} /></label
		>
		<label class="field"><span>{t('peer.f.remoteLabel')}</span><input bind:value={f.remote_label} /></label>
	</div>
	<div class="row">
		<label class="field inline" style="align-items:center; gap:0.5rem">
			<input type="checkbox" bind:checked={f.is_internal} /> <span style="margin:0">{t('peer.f.internal')}</span>
		</label>
		<label class="field inline" style="align-items:center; gap:0.5rem">
			<input type="checkbox" bind:checked={f.enabled} /> <span style="margin:0">{t('peer.f.enabled')}</span>
		</label>
	</div>
	<label class="field"><span>{t('peer.f.notes')}</span><input bind:value={f.notes} /></label>

	{#snippet footer()}
		<button class="btn" onclick={() => (showForm = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={save} disabled={saving}>
			{saving ? t('common.saving') : t('common.save')}
		</button>
	{/snippet}
</Modal>
