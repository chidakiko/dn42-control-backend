<script lang="ts">
	// Generic CRUD tab for resources that carry a JSON `spec` plus a few flags:
	// interfaces, BGP sessions and DNS zones all fit this shape. The differences
	// (which top-level flags exist) are passed via `fields`.
	import { onMount } from 'svelte';
	import { errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import Modal from './../Modal.svelte';
	import JsonEditor from './../JsonEditor.svelte';
	import JsonView from './../JsonView.svelte';
	import type { PeeringOut } from '$lib/types';

	interface SpecItem {
		id: number;
		name: string;
		enabled?: boolean;
		sort_order?: number;
		kind?: string;
		remote_asn?: number;
		peering_id?: number | null;
		spec: Record<string, unknown>;
	}

	let {
		title,
		singular,
		fields,
		example,
		peerings = [],
		load,
		create,
		update,
		remove
	}: {
		title: string;
		singular: string;
		fields: { enabled?: boolean; sortOrder?: boolean; peering?: boolean };
		example: string;
		peerings?: PeeringOut[];
		load: () => Promise<SpecItem[]>;
		create: (body: Record<string, unknown>) => Promise<unknown>;
		update: (id: number, body: Record<string, unknown>) => Promise<unknown>;
		remove: (id: number) => Promise<void>;
	} = $props();

	let items = $state<SpecItem[]>([]);
	let loading = $state(true);
	let err = $state('');

	let showForm = $state(false);
	let editing = $state<SpecItem | null>(null);
	let saving = $state(false);
	let viewing = $state<SpecItem | null>(null);
	let showView = $state(false);

	function view(it: SpecItem) {
		viewing = it;
		showView = true;
	}

	// form state
	let specText = $state('');
	let enabled = $state(true);
	let sortOrder = $state('0');
	let peeringId = $state<string>('');
	let editor: JsonEditor;

	async function refresh() {
		loading = true;
		err = '';
		try {
			items = await load();
		} catch (e) {
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	onMount(refresh);

	function openCreate() {
		editing = null;
		specText = example;
		enabled = true;
		sortOrder = '0';
		peeringId = '';
		showForm = true;
	}

	function openEdit(it: SpecItem) {
		editing = it;
		specText = JSON.stringify(it.spec, null, 2);
		enabled = it.enabled ?? true;
		sortOrder = String(it.sort_order ?? 0);
		peeringId = it.peering_id != null ? String(it.peering_id) : '';
		showForm = true;
	}

	function buildBody(): Record<string, unknown> {
		const body: Record<string, unknown> = { spec: JSON.parse(specText) };
		if (fields.enabled) body.enabled = enabled;
		if (fields.sortOrder) body.sort_order = Number(sortOrder);
		if (fields.peering) {
			if (peeringId) body.peering_id = Number(peeringId);
			else if (editing) body.clear_peering = true;
		}
		return body;
	}

	async function save() {
		if (!editor.valid()) {
			toast.error(t('spec.badSpec'));
			return;
		}
		saving = true;
		try {
			if (editing) {
				await update(editing.id, buildBody());
				toast.success(t('spec.updatedOf', singular));
			} else {
				await create(buildBody());
				toast.success(t('spec.createdOf', singular));
			}
			showForm = false;
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			saving = false;
		}
	}

	async function del(it: SpecItem) {
		if (!confirm(t('spec.confirmDelete', singular, it.name))) return;
		try {
			await remove(it.id);
			toast.success(t('spec.deletedOf', singular));
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}
</script>

<div class="card-head">
	<h3>{title} <span class="faint">({items.length})</span></h3>
	<div class="inline">
		<button class="btn sm" onclick={refresh} disabled={loading}>↻</button>
		<button class="btn primary sm" onclick={openCreate}>+ {t('common.add')}</button>
	</div>
</div>

{#if loading && items.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if err}
	<p class="error-text">{err}</p>
{:else if items.length === 0}
	<div class="empty">{t('spec.emptyOf', title)}</div>
{:else}
	<table>
		<thead>
			<tr>
				<th>{t('spec.col.name')}</th>
				{#if fields.enabled}<th>{t('spec.col.enabled')}</th>{/if}
				<th>{t('spec.col.details')}</th>
				{#if fields.sortOrder}<th>{t('spec.col.sort')}</th>{/if}
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each items as it (it.id)}
				<tr>
					<td class="mono">{it.name}</td>
					{#if fields.enabled}
						<td>
							<span class="badge {it.enabled ? 'ok' : 'unknown'}"
								><span class="dot"></span>{it.enabled ? t('common.on') : t('common.off2')}</span
							>
						</td>
					{/if}
					<td class="faint">
						{#if it.kind}<span class="tag">{it.kind}</span>{/if}
						{#if it.remote_asn}<span class="tag">AS{it.remote_asn}</span>{/if}
						{#if it.peering_id != null}<span class="tag">peering #{it.peering_id}</span>{/if}
					</td>
					{#if fields.sortOrder}<td class="mono">{it.sort_order ?? 0}</td>{/if}
					<td class="actions">
						<button class="btn ghost sm" onclick={() => view(it)}>{t('common.view')}</button>
						<button class="btn ghost sm" onclick={() => openEdit(it)}>{t('common.edit')}</button>
						<button class="btn ghost sm danger" onclick={() => del(it)}>{t('common.delete')}</button>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<Modal title={editing ? t('spec.editOf', singular) : t('spec.addOf', singular)} bind:open={showForm}>
	{#if fields.enabled || fields.sortOrder || fields.peering}
		<div class="row">
			{#if fields.enabled}
				<label class="field inline" style="align-items:center; gap:0.5rem">
					<input type="checkbox" bind:checked={enabled} /> <span style="margin:0">{t('spec.enabled')}</span>
				</label>
			{/if}
			{#if fields.sortOrder}
				<label class="field"><span>{t('spec.sortOrder')}</span><input bind:value={sortOrder} /></label>
			{/if}
			{#if fields.peering}
				<label class="field">
					<span>{t('spec.peering')}</span>
					<select bind:value={peeringId}>
						<option value="">{t('spec.peeringNone')}</option>
						{#each peerings as p (p.id)}
							<option value={String(p.id)}>#{p.id} {p.name}</option>
						{/each}
					</select>
				</label>
			{/if}
		</div>
	{/if}
	<JsonEditor bind:this={editor} bind:text={specText} label="spec" rows={16} />
	<p class="faint" style="font-size:0.75rem">{t('spec.specHint')}</p>

	{#snippet footer()}
		<button class="btn" onclick={() => (showForm = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={save} disabled={saving}>
			{saving ? t('common.saving') : t('common.save')}
		</button>
	{/snippet}
</Modal>

<Modal title={viewing ? viewing.name : ''} bind:open={showView}>
	{#if viewing}
		<JsonView value={viewing.spec} max />
	{/if}
</Modal>
