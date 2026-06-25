<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import type { Registration } from '$lib/types';
	import Modal from '$lib/components/Modal.svelte';
	import JsonView from '$lib/components/JsonView.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';

	let items = $state<Registration[]>([]);
	let loading = $state(true);
	let error = $state('');
	let filter = $state('pending');

	let showInv = $state(false);
	let inv = $state<unknown>(null);

	async function load() {
		if (items.length === 0) loading = true;
		error = '';
		try {
			const r = await api.listRegistrations(filter || undefined);
			items = r.registrations;
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		autoRefresh.tick;
		filter; // reload when the status filter changes
		untrack(() => load());
	});

	async function decide(r: Registration, action: 'approve' | 'reject') {
		const label = action === 'approve' ? t('reg.approve') : t('reg.reject');
		const note = prompt(t('reg.notePrompt', label)) ?? undefined;
		try {
			if (action === 'approve') await api.approveRegistration(r.id, note);
			else await api.rejectRegistration(r.id, note);
			toast.success(t('reg.decided', action === 'approve' ? t('reg.approved') : t('reg.rejected')));
			await load();
		} catch (err) {
			toast.error(errorMessage(err));
		}
	}

	function statusCls(s: string): string {
		return s === 'approved' ? 'ok' : s === 'rejected' ? 'bad' : 'stale';
	}
</script>

<div class="page-head">
	<div>
		<div class="ph-title">
			<Icon name="registrations" size={22} />
			<h1>{t('reg.title')}</h1>
		</div>
		<p class="ph-sub">{t('reg.subtitle')}</p>
	</div>
	<div class="ph-actions">
		<select bind:value={filter} onchange={load} style="width:auto">
			<option value="">{t('reg.all')}</option>
			<option value="pending">{t('reg.pending')}</option>
			<option value="approved">{t('reg.approved')}</option>
			<option value="rejected">{t('reg.rejected')}</option>
		</select>
		<button class="btn sm" onclick={load} disabled={loading}>
			<Icon name="refresh" size={15} />{t('common.refresh')}
		</button>
	</div>
</div>

<p class="faint" style="font-size:0.8rem; margin-top:-0.75rem">{t('reg.note')}</p>

{#if loading && items.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else}
	<div class="card" style="padding:0">
		{#if items.length === 0}
			<EmptyState icon="registrations" title={t('reg.empty')} hint={t('reg.subtitle')} />
		{:else}
			<table>
				<thead>
					<tr><th>{t('reg.col.id')}</th><th>{t('reg.col.node')}</th><th>{t('reg.col.status')}</th><th>{t('reg.col.created')}</th><th></th></tr>
				</thead>
				<tbody>
					{#each items as r (r.id)}
						<tr>
							<td class="mono">{r.id}</td>
							<td class="mono">{r.node_id}</td>
							<td><span class="badge {statusCls(r.status)}"><span class="dot"></span>{t('reg.' + r.status)}</span></td>
							<td class="faint">{fmtTime(r.created_at)}</td>
							<td class="actions">
								{#if r.inventory}
									<button class="btn ghost sm" onclick={() => { inv = r.inventory; showInv = true; }}>
										{t('reg.inventory')}
									</button>
								{/if}
								{#if r.status === 'pending'}
									<button class="btn ghost sm" onclick={() => decide(r, 'approve')}>{t('reg.approve')}</button>
									<button class="btn ghost sm danger" onclick={() => decide(r, 'reject')}>{t('reg.reject')}</button>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
{/if}

<Modal title={t('reg.invTitle')} bind:open={showInv}>
	<JsonView value={inv} max />
</Modal>
