<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { autoRefresh } from '$lib/refresh.svelte';
	import type { NodeIn, NodeOut } from '$lib/types';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import JsonEditor from '$lib/components/JsonEditor.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { fade } from 'svelte/transition';

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
		link_local: '',
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
	$effect(() => {
		autoRefresh.tick;
		untrack(() => load());
	});

	function resetForm() {
		f = {
			node_id: '',
			asn: '',
			router_id: '',
			site: '',
			loopback_ipv4: '',
			loopback_ipv6: '',
			link_local: '',
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
			link_local: f.link_local.trim() || null,
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

<div class="page-head" style="justify-content:flex-end">
	<div class="ph-actions">
		<button class="btn primary sm" onclick={() => (showCreate = true)}>+ {t('nodes.new')}</button>
	</div>
</div>

{#if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else if !loading && nodes.length === 0}
	<div class="card" style="padding:0" in:fade={{ duration: 150 }}>
		<EmptyState
			icon="nodes"
			title={t('nodes.empty')}
			hint={t('nodes.subtitle')}
			actionLabel={'+ ' + t('nodes.new')}
			onaction={() => (showCreate = true)}
		/>
	</div>
{:else}
	<div class="card" style="padding:0">
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
				{#if loading && nodes.length === 0}
					{#each Array(6) as _, i (i)}
						<tr>
							<td><Skeleton w="6rem" h="0.85rem" /></td>
							<td><Skeleton w="5rem" h="0.85rem" /></td>
							<td><Skeleton w="5.5rem" h="0.85rem" /></td>
							<td><Skeleton w="2.5rem" h="0.85rem" /></td>
							<td><Skeleton w="4.5rem" h="1.2rem" radius="999px" /></td>
							<td><Skeleton w="1.2rem" h="0.85rem" /></td>
							<td><Skeleton w="6rem" h="0.8rem" /></td>
						</tr>
					{/each}
				{:else}
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
				{/if}
			</tbody>
		</table>
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
		<label class="field"><span>{t('node.f.lla')}</span><input bind:value={f.link_local} placeholder="fe80::28" /></label>
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
