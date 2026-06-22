<script lang="ts">
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import type { NodeIn, NodeOut } from '$lib/types';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import JsonEditor from '$lib/components/JsonEditor.svelte';

	let nodes = $state<NodeOut[]>([]);
	let loading = $state(true);
	let error = $state('');

	let showCreate = $state(false);
	let saving = $state(false);

	// create form
	let f = $state({
		node_id: '',
		asn: '',
		router_id: '',
		site: '',
		loopback_ipv4: '',
		loopback_ipv6: '',
		ipv4_prefixes: '',
		ipv6_prefixes: ''
	});
	let baseTemplate = $state('{}');
	let baseEditor: JsonEditor;

	async function load() {
		loading = true;
		error = '';
		try {
			nodes = await api.listNodes();
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
	}
	onMount(load);

	function resetForm() {
		f = {
			node_id: '',
			asn: '',
			router_id: '',
			site: '',
			loopback_ipv4: '',
			loopback_ipv6: '',
			ipv4_prefixes: '',
			ipv6_prefixes: ''
		};
		baseTemplate = '{}';
	}

	function lines(s: string): string[] {
		return s
			.split(/[\n,]/)
			.map((x) => x.trim())
			.filter(Boolean);
	}

	async function create() {
		if (!f.node_id.trim() || !f.asn.trim() || !f.router_id.trim()) {
			toast.error(t('nodes.req'));
			return;
		}
		if (!baseEditor.valid()) {
			toast.error(t('nodes.badBase'));
			return;
		}
		const body: NodeIn = {
			node_id: f.node_id.trim(),
			asn: Number(f.asn),
			router_id: f.router_id.trim(),
			site: f.site.trim() || null,
			loopback_ipv4: f.loopback_ipv4.trim() || null,
			loopback_ipv6: f.loopback_ipv6.trim() || null,
			ipv4_prefixes: lines(f.ipv4_prefixes),
			ipv6_prefixes: lines(f.ipv6_prefixes),
			base_template: JSON.parse(baseTemplate || '{}')
		};
		saving = true;
		try {
			await api.createNode(body);
			toast.success(t('nodes.created', body.node_id));
			showCreate = false;
			resetForm();
			await load();
		} catch (err) {
			toast.error(errorMessage(err));
		} finally {
			saving = false;
		}
	}
</script>

<div class="spread" style="margin-bottom:1.25rem">
	<h1>{t('nodes.title')}</h1>
	<div class="inline">
		<button class="btn sm" onclick={load} disabled={loading}>↻ {t('common.refresh')}</button>
		<button class="btn primary sm" onclick={() => (showCreate = true)}>+ {t('nodes.new')}</button>
	</div>
</div>

{#if loading && nodes.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else}
	<div class="card" style="padding:0">
		{#if nodes.length === 0}
			<div class="empty">{t('nodes.empty')}</div>
		{:else}
			<table>
				<thead>
					<tr>
						<th>{t('nodes.col.id')}</th>
						<th>{t('nodes.col.asn')}</th>
						<th>{t('nodes.col.routerId')}</th>
						<th>{t('nodes.col.site')}</th>
						<th>{t('nodes.col.lifecycle')}</th>
						<th>{t('nodes.col.gen')}</th>
						<th>{t('nodes.col.updated')}</th>
					</tr>
				</thead>
				<tbody>
					{#each nodes as n (n.node_id)}
						<tr>
							<td><a href="/nodes/{n.node_id}" class="mono">{n.node_id}</a></td>
							<td class="mono">{n.asn}</td>
							<td class="mono">{n.router_id}</td>
							<td>{n.site ?? '—'}</td>
							<td>
								<span class="badge {n.lifecycle === 'active' ? 'ok' : 'stale'}">
									<span class="dot"></span>{n.lifecycle}
								</span>
							</td>
							<td class="mono">{n.current_generation}</td>
							<td class="faint">{fmtTime(n.updated_at)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
{/if}

<Modal title={t('nodes.create')} bind:open={showCreate}>
	<div class="row">
		<label class="field"><span>{t('nodes.col.id')} *</span><input bind:value={f.node_id} placeholder="hkg2-edge" /></label>
		<label class="field"><span>{t('nodes.col.asn')} *</span><input bind:value={f.asn} placeholder="4242420000" /></label>
	</div>
	<div class="row">
		<label class="field"><span>{t('nodes.col.routerId')} *</span><input bind:value={f.router_id} placeholder="172.20.0.1" /></label>
		<label class="field"><span>{t('nodes.col.site')}</span><input bind:value={f.site} placeholder="hkg" /></label>
	</div>
	<div class="row">
		<label class="field"><span>{t('node.f.lo4')}</span><input bind:value={f.loopback_ipv4} /></label>
		<label class="field"><span>{t('node.f.lo6')}</span><input bind:value={f.loopback_ipv6} /></label>
	</div>
	<div class="row">
		<label class="field"
			><span>{t('nodes.f.ipv4')}</span><textarea bind:value={f.ipv4_prefixes} rows="2"
			></textarea></label
		>
		<label class="field"
			><span>{t('nodes.f.ipv6')}</span><textarea bind:value={f.ipv6_prefixes} rows="2"
			></textarea></label
		>
	</div>
	<JsonEditor bind:this={baseEditor} bind:text={baseTemplate} label={t('nodes.f.base')} rows={8} />

	{#snippet footer()}
		<button class="btn" onclick={() => (showCreate = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={create} disabled={saving}>
			{saving ? t('common.creating') : t('nodes.create')}
		</button>
	{/snippet}
</Modal>
