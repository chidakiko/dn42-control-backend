<script module lang="ts">
	// Field schema for the structured spec editor. When a caller passes `formFields`,
	// the add/edit modal renders real inputs instead of raw JSON; an "advanced" toggle
	// still drops to JSON for anything the form doesn't cover.
	export interface SpecField {
		key: string;
		label: string;
		type: 'text' | 'number' | 'bool' | 'select' | 'list' | 'group';
		placeholder?: string;
		required?: boolean;
		mono?: boolean;
		options?: string[];
		fields?: SpecField[];
	}
</script>

<script lang="ts">
	// Generic CRUD tab for resources that carry a JSON `spec` plus a few flags:
	// interfaces, BGP sessions and DNS zones all fit this shape. The differences
	// (which top-level flags exist) are passed via `fields`; an optional `formFields`
	// turns the spec editor from raw JSON into a structured form.
	import { onMount } from 'svelte';
	import { errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import Modal from './../Modal.svelte';
	import JsonEditor from './../JsonEditor.svelte';
	import JsonView from './../JsonView.svelte';
	import Icon from './../Icon.svelte';
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
		formFields,
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
		formFields?: SpecField[];
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
	let editor = $state<JsonEditor>();
	// structured-form state: `form` is keyed by field key; advanced toggle drops to JSON.
	let form = $state<Record<string, any>>({});
	let jsonMode = $state(false);

	// --- spec <-> form conversion ---
	function specToForm(spec: Record<string, unknown>, defs: SpecField[]): Record<string, any> {
		const out: Record<string, any> = {};
		for (const f of defs) {
			const v = spec?.[f.key];
			if (f.type === 'list') out[f.key] = Array.isArray(v) ? v.join('\n') : '';
			else if (f.type === 'group') out[f.key] = specToForm((v as Record<string, unknown>) ?? {}, f.fields ?? []);
			else if (f.type === 'bool') out[f.key] = v === undefined ? false : !!v;
			else out[f.key] = v ?? '';
		}
		return out;
	}

	function formToSpec(fm: Record<string, any>, defs: SpecField[]): Record<string, unknown> {
		const out: Record<string, unknown> = {};
		for (const f of defs) {
			const v = fm[f.key];
			if (f.type === 'number') {
				if (v === '' || v == null) continue;
				out[f.key] = Number(v);
			} else if (f.type === 'bool') {
				out[f.key] = !!v;
			} else if (f.type === 'list') {
				const arr = String(v ?? '')
					.split(/[\n,]/)
					.map((x) => x.trim())
					.filter(Boolean);
				out[f.key] = arr;
			} else if (f.type === 'group') {
				out[f.key] = formToSpec(v ?? {}, f.fields ?? []);
			} else {
				const s = String(v ?? '').trim();
				if (s === '') continue;
				out[f.key] = s;
			}
		}
		return out;
	}

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

	function seedForm(spec: Record<string, unknown>) {
		if (formFields) form = specToForm(spec, formFields);
		specText = JSON.stringify(spec, null, 2);
		jsonMode = !formFields;
	}

	function openCreate() {
		editing = null;
		let base: Record<string, unknown> = {};
		try {
			base = JSON.parse(example);
		} catch {
			base = {};
		}
		seedForm(base);
		enabled = true;
		sortOrder = '0';
		peeringId = '';
		showForm = true;
	}

	function openEdit(it: SpecItem) {
		editing = it;
		seedForm(it.spec);
		enabled = it.enabled ?? true;
		sortOrder = String(it.sort_order ?? 0);
		peeringId = it.peering_id != null ? String(it.peering_id) : '';
		showForm = true;
	}

	// Keep the raw-JSON view in sync when the user expands "advanced" from form mode.
	function toJsonMode() {
		if (formFields) specText = JSON.stringify(formToSpec(form, formFields), null, 2);
		jsonMode = true;
	}
	function toFormMode() {
		if (!editor?.valid()) {
			toast.error(t('spec.badSpec'));
			return;
		}
		if (formFields) form = specToForm(JSON.parse(specText), formFields);
		jsonMode = false;
	}

	function buildBody(): Record<string, unknown> {
		const spec = jsonMode || !formFields ? JSON.parse(specText) : formToSpec(form, formFields);
		const body: Record<string, unknown> = { spec };
		if (fields.enabled) body.enabled = enabled;
		if (fields.sortOrder) body.sort_order = Number(sortOrder);
		if (fields.peering) {
			if (peeringId) body.peering_id = Number(peeringId);
			else if (editing) body.clear_peering = true;
		}
		return body;
	}

	async function save() {
		if ((jsonMode || !formFields) && !editor?.valid()) {
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
		<button class="btn sm" onclick={refresh} disabled={loading} aria-label={t('common.refresh')}>
			<Icon name="refresh" size={15} />
		</button>
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

	{#if formFields && !jsonMode}
		<div class="spec-form">
			{#each formFields as f (f.key)}
				{#if f.type === 'group'}
					<fieldset class="group">
						<legend>{f.label}</legend>
						<div class="spec-form">
							{#each f.fields ?? [] as sf (sf.key)}
								{@render fieldInput(sf, form[f.key])}
							{/each}
						</div>
					</fieldset>
				{:else}
					{@render fieldInput(f, form)}
				{/if}
			{/each}
		</div>
		<button type="button" class="btn ghost sm advanced-toggle" onclick={toJsonMode}>
			<Icon name="chevron-down" size={14} />{t('spec.advancedJson')}
		</button>
	{:else}
		{#if formFields}
			<button type="button" class="btn ghost sm advanced-toggle" onclick={toFormMode}>
				<Icon name="arrow-left" size={14} />{t('spec.backToForm')}
			</button>
		{/if}
		<JsonEditor bind:this={editor} bind:text={specText} label="spec" rows={16} />
		<p class="faint" style="font-size:0.75rem">{t('spec.specHint')}</p>
	{/if}

	{#snippet footer()}
		<button class="btn" onclick={() => (showForm = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={save} disabled={saving}>
			{saving ? t('common.saving') : t('common.save')}
		</button>
	{/snippet}
</Modal>

{#snippet fieldInput(f: SpecField, target: Record<string, any>)}
	{#if f.type === 'bool'}
		<label class="field inline" style="align-items:center; gap:0.5rem">
			<input type="checkbox" bind:checked={target[f.key]} />
			<span style="margin:0">{f.label}</span>
		</label>
	{:else if f.type === 'select'}
		<label class="field">
			<span>{f.label}</span>
			<select bind:value={target[f.key]}>
				{#each f.options ?? [] as o (o)}<option value={o}>{o}</option>{/each}
			</select>
		</label>
	{:else if f.type === 'list'}
		<label class="field">
			<span>{f.label}</span>
			<textarea
				bind:value={target[f.key]}
				rows="2"
				placeholder={f.placeholder}
				style="font-family:var(--mono); min-height:auto"
			></textarea>
		</label>
	{:else if f.type === 'number'}
		<label class="field">
			<span>{f.label}{f.required ? ' *' : ''}</span>
			<input type="number" bind:value={target[f.key]} placeholder={f.placeholder} />
		</label>
	{:else}
		<label class="field">
			<span>{f.label}{f.required ? ' *' : ''}</span>
			<input
				bind:value={target[f.key]}
				placeholder={f.placeholder}
				class:mono={f.mono}
			/>
		</label>
	{/if}
{/snippet}

<Modal title={viewing ? viewing.name : ''} bind:open={showView}>
	{#if viewing}
		<JsonView value={viewing.spec} max />
	{/if}
</Modal>

<style>
	.spec-form {
		display: flex;
		flex-direction: column;
		gap: 0.7rem;
	}
	.spec-form :global(.field) {
		margin-bottom: 0;
	}
	.group {
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 0.8rem 0.9rem;
		margin: 0;
	}
	.group legend {
		padding: 0 0.4rem;
		color: var(--text-dim);
		font-size: 0.8rem;
		font-weight: 600;
	}
	.advanced-toggle {
		margin-top: 0.8rem;
		color: var(--text-dim);
	}
</style>
