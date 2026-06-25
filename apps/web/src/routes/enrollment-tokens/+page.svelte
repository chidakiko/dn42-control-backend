<script lang="ts">
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import type { EnrollmentTokenOut } from '$lib/types';
	import Modal from '$lib/components/Modal.svelte';
	import SecretReveal from '$lib/components/SecretReveal.svelte';

	let items = $state<EnrollmentTokenOut[]>([]);
	let loading = $state(true);
	let error = $state('');

	let showCreate = $state(false);
	let saving = $state(false);
	let f = $state({ node_id: '', description: '', expires_at: '', token: '' });
	let secret = $state<string | null>(null);

	async function load() {
		loading = true;
		error = '';
		try {
			items = await api.listEnrollmentTokens();
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
	}
	onMount(load);

	async function create() {
		saving = true;
		try {
			const body: Record<string, unknown> = {};
			if (f.node_id.trim()) body.node_id = f.node_id.trim();
			if (f.description.trim()) body.description = f.description.trim();
			if (f.token.trim()) body.token = f.token.trim();
			if (f.expires_at) body.expires_at = new Date(f.expires_at).toISOString();
			const created = await api.createEnrollmentToken(body);
			secret = created.secret;
			showCreate = false;
			f = { node_id: '', description: '', expires_at: '', token: '' };
			await load();
		} catch (err) {
			toast.error(errorMessage(err));
		} finally {
			saving = false;
		}
	}

	async function del(tok: EnrollmentTokenOut) {
		if (!confirm(t('enr.confirmDelete', tok.token_id))) return;
		try {
			await api.deleteEnrollmentToken(tok.token_id);
			toast.success(t('enr.deleted'));
			await load();
		} catch (err) {
			toast.error(errorMessage(err));
		}
	}

	function tokenState(tok: EnrollmentTokenOut): { cls: string; label: string } {
		if (tok.used_at) return { cls: 'unknown', label: t('enr.used') };
		if (tok.expires_at && new Date(tok.expires_at).getTime() < Date.now())
			return { cls: 'stale', label: t('enr.expired') };
		return { cls: 'ok', label: t('enr.valid') };
	}
</script>

<div class="page-head">
	<div>
		<div class="ph-title">
			<Icon name="tokens" size={22} />
			<h1>{t('enr.title')}</h1>
		</div>
		<p class="ph-sub">{t('enr.note')}</p>
	</div>
	<div class="ph-actions">
		<button class="btn sm" onclick={load} disabled={loading}>
			<Icon name="refresh" size={15} />{t('common.refresh')}
		</button>
		<button class="btn primary sm" onclick={() => (showCreate = true)}>+ {t('enr.new')}</button>
	</div>
</div>

{#if loading && items.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else}
	<div class="card" style="padding:0">
		{#if items.length === 0}
			<EmptyState
				icon="tokens"
				title={t('enr.empty')}
				hint={t('enr.note')}
				actionLabel={'+ ' + t('enr.new')}
				onaction={() => (showCreate = true)}
			/>
		{:else}
			<table>
				<thead>
					<tr><th>{t('enr.col.id')}</th><th>{t('enr.col.node')}</th><th>{t('enr.col.desc')}</th><th>{t('enr.col.state')}</th><th>{t('enr.col.expires')}</th><th>{t('enr.col.created')}</th><th></th></tr>
				</thead>
				<tbody>
					{#each items as tok (tok.token_id)}
						{@const s = tokenState(tok)}
						<tr>
							<td class="mono">{tok.token_id}</td>
							<td class="mono faint">{tok.node_id ?? t('enr.any')}</td>
							<td>{tok.description ?? '—'}</td>
							<td><span class="badge {s.cls}"><span class="dot"></span>{s.label}</span></td>
							<td class="faint">{tok.expires_at ? fmtTime(tok.expires_at) : t('common.never')}</td>
							<td class="faint">{fmtTime(tok.created_at)}</td>
							<td class="actions">
								<button class="btn ghost sm danger" onclick={() => del(tok)}>{t('common.delete')}</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
{/if}

<Modal title={t('enr.create')} bind:open={showCreate}>
	<label class="field"><span>{t('enr.f.node')}</span><input bind:value={f.node_id} /></label>
	<label class="field"><span>{t('enr.f.desc')}</span><input bind:value={f.description} /></label>
	<label class="field"><span>{t('enr.f.expires')}</span><input type="datetime-local" bind:value={f.expires_at} /></label>
	<label class="field"><span>{t('enr.f.token')}</span><input bind:value={f.token} /></label>
	{#snippet footer()}
		<button class="btn" onclick={() => (showCreate = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={create} disabled={saving}>
			{saving ? t('common.creating') : t('common.create')}
		</button>
	{/snippet}
</Modal>

<SecretReveal bind:secret label={t('secret.enrollment')} />
