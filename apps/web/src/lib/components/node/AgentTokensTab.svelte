<script lang="ts">
	import { onMount } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import type { AgentTokenOut } from '$lib/types';
	import Modal from './../Modal.svelte';
	import SecretReveal from './../SecretReveal.svelte';

	let { nodeId }: { nodeId: string } = $props();

	let items = $state<AgentTokenOut[]>([]);
	let loading = $state(true);
	let err = $state('');

	let showIssue = $state(false);
	let ttl = $state('');
	let customAgentId = $state('');
	let saving = $state(false);

	let secret = $state<string | null>(null);

	async function refresh() {
		loading = true;
		err = '';
		try {
			items = await api.listAgentTokens(nodeId);
		} catch (e) {
			err = errorMessage(e);
		} finally {
			loading = false;
		}
	}
	onMount(refresh);

	function status(tok: AgentTokenOut): { cls: string; label: string } {
		if (tok.revoked_at) return { cls: 'bad', label: t('tok.revoked') };
		if (tok.expires_at && new Date(tok.expires_at).getTime() < Date.now())
			return { cls: 'stale', label: t('tok.expired') };
		return { cls: 'ok', label: t('tok.active') };
	}

	async function issue() {
		saving = true;
		try {
			const body: Record<string, unknown> = {};
			if (ttl.trim()) body.ttl_seconds = Number(ttl);
			if (customAgentId.trim()) body.agent_id = customAgentId.trim();
			const t = await api.issueAgentToken(nodeId, body);
			secret = t.token; // one-time full secret
			showIssue = false;
			ttl = '';
			customAgentId = '';
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		} finally {
			saving = false;
		}
	}

	async function rotate(tok: AgentTokenOut) {
		if (!confirm(t('tok.confirmRotate', tok.token))) return;
		try {
			const nt = await api.rotateAgentToken(tok.token);
			secret = nt.token;
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}

	async function revoke(tok: AgentTokenOut) {
		if (!confirm(t('tok.confirmRevoke', tok.token))) return;
		try {
			await api.revokeAgentToken(tok.token);
			toast.success(t('tok.revokedMsg'));
			await refresh();
		} catch (e) {
			toast.error(errorMessage(e));
		}
	}
</script>

<div class="card-head">
	<h3>{t('tok.title')} <span class="faint">({items.length})</span></h3>
	<div class="inline">
		<button class="btn sm" onclick={refresh} disabled={loading}>↻</button>
		<button class="btn primary sm" onclick={() => (showIssue = true)}>+ {t('tok.issue')}</button>
	</div>
</div>

{#if loading && items.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if err}
	<p class="error-text">{err}</p>
{:else if items.length === 0}
	<div class="empty">{t('tok.empty')}</div>
{:else}
	<table>
		<thead>
			<tr><th>{t('tok.col.id')}</th><th>{t('tok.col.agent')}</th><th>{t('tok.col.status')}</th><th>{t('tok.col.issued')}</th><th>{t('tok.col.expires')}</th><th></th></tr>
		</thead>
		<tbody>
			{#each items as tk (tk.token)}
				{@const s = status(tk)}
				<tr>
					<td class="mono">{tk.token}</td>
					<td class="mono faint">{tk.agent_id}</td>
					<td><span class="badge {s.cls}"><span class="dot"></span>{s.label}</span></td>
					<td class="faint">{fmtTime(tk.issued_at)}</td>
					<td class="faint">{tk.expires_at ? fmtTime(tk.expires_at) : t('common.never')}</td>
					<td class="actions">
						<button class="btn ghost sm" onclick={() => rotate(tk)} disabled={!!tk.revoked_at}>
							{t('tok.rotate')}
						</button>
						<button class="btn ghost sm danger" onclick={() => revoke(tk)} disabled={!!tk.revoked_at}>
							{t('tok.revoke')}
						</button>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<Modal title={t('tok.issueTitle')} bind:open={showIssue}>
	<label class="field"><span>{t('tok.f.agentId')}</span><input bind:value={customAgentId} /></label>
	<label class="field"
		><span>{t('tok.f.ttl')}</span><input bind:value={ttl} /></label
	>
	{#snippet footer()}
		<button class="btn" onclick={() => (showIssue = false)}>{t('common.cancel')}</button>
		<button class="btn primary" onclick={issue} disabled={saving}>
			{saving ? t('common.saving') : t('tok.issueBtn')}
		</button>
	{/snippet}
</Modal>

<SecretReveal bind:secret label={t('secret.agent')} />
