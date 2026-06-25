<script lang="ts">
	import { untrack } from 'svelte';
	import { api, errorMessage } from '$lib/api';
	import { fmtTime } from '$lib/format';
	import { t } from '$lib/i18n.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import type { AuditEntry } from '$lib/types';
	import Modal from '$lib/components/Modal.svelte';
	import JsonView from '$lib/components/JsonView.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';

	let items = $state<AuditEntry[]>([]);
	let loading = $state(true);
	let error = $state('');
	let limit = $state(100);

	let showDetail = $state(false);
	let detail = $state<unknown>(null);

	async function load() {
		if (items.length === 0) loading = true;
		error = '';
		try {
			const r = await api.auditLog(limit);
			items = r.entries;
		} catch (err) {
			error = errorMessage(err);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		autoRefresh.tick;
		limit; // reload when the row limit changes
		untrack(() => load());
	});

	function codeCls(code: number): string {
		if (code >= 500) return 'bad';
		if (code >= 400) return 'stale';
		if (code >= 200 && code < 300) return 'ok';
		return 'neutral';
	}
</script>

<div class="page-head">
	<div>
		<div class="ph-title">
			<Icon name="audit" size={22} />
			<h1>{t('audit.title')}</h1>
		</div>
		<p class="ph-sub">{t('audit.subtitle')}</p>
	</div>
	<div class="ph-actions">
		<select bind:value={limit} onchange={load} style="width:auto">
			<option value={50}>{t('audit.last', 50)}</option>
			<option value={100}>{t('audit.last', 100)}</option>
			<option value={250}>{t('audit.last', 250)}</option>
			<option value={1000}>{t('audit.last', 1000)}</option>
		</select>
		<button class="btn sm" onclick={load} disabled={loading}>
			<Icon name="refresh" size={15} />{t('common.refresh')}
		</button>
	</div>
</div>

<p class="faint" style="font-size:0.8rem; margin-top:-0.75rem">{t('audit.note')}</p>

{#if loading && items.length === 0}
	<div class="empty">{t('common.loading')}</div>
{:else if error}
	<div class="card"><p class="error-text">{error}</p></div>
{:else}
	<div class="card" style="padding:0">
		{#if items.length === 0}
			<EmptyState icon="audit" title={t('audit.empty')} hint={t('audit.subtitle')} />
		{:else}
			<table>
				<thead>
					<tr><th>{t('audit.col.when')}</th><th>{t('audit.col.actor')}</th><th>{t('audit.col.method')}</th><th>{t('audit.col.path')}</th><th>{t('audit.col.status')}</th><th></th></tr>
				</thead>
				<tbody>
					{#each items as a (a.id)}
						<tr>
							<td class="faint">{fmtTime(a.created_at)}</td>
							<td class="mono faint">{a.actor ?? '—'}</td>
							<td><span class="tag">{a.method}</span></td>
							<td class="mono" style="word-break:break-all">{a.path}</td>
							<td><span class="badge {codeCls(a.status_code)}"><span class="dot"></span>{a.status_code}</span></td>
							<td class="actions">
								{#if a.detail && Object.keys(a.detail).length}
									<button class="btn ghost sm" onclick={() => { detail = a.detail; showDetail = true; }}>
										{t('audit.detail')}
									</button>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
{/if}

<Modal title={t('audit.detailTitle')} bind:open={showDetail}>
	<JsonView value={detail} max />
</Modal>
