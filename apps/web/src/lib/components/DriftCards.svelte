<script lang="ts">
	// Renders a reconcile report's drift list as severity-ranked cards instead of
	// raw JSON: a coloured severity pill + component·name header, the message, and
	// a desired → observed diff line when present. Mirrors dn42_schemas.DriftItem.
	import { t } from '$lib/i18n.svelte';
	import type { DriftItem } from '$lib/types';

	let { items }: { items: DriftItem[] } = $props();

	const ORDER: Record<string, number> = { critical: 0, warning: 1, info: 2 };
	function sevClass(s: string): string {
		return s === 'critical' ? 'crit' : s === 'warning' ? 'warn' : 'info';
	}
	// Critical first, then warning, then info; stable within a severity.
	let sorted = $derived(
		[...items].sort((a, b) => (ORDER[a.severity] ?? 9) - (ORDER[b.severity] ?? 9))
	);
	let counts = $derived.by(() => {
		const c = { critical: 0, warning: 0, info: 0 } as Record<string, number>;
		for (const d of items) c[d.severity] = (c[d.severity] ?? 0) + 1;
		return c;
	});
</script>

<div class="drift">
	<div class="summary">
		{#if counts.critical}<span class="cnt crit">{counts.critical} {t('drift.sev.critical')}</span>{/if}
		{#if counts.warning}<span class="cnt warn">{counts.warning} {t('drift.sev.warning')}</span>{/if}
		{#if counts.info}<span class="cnt info">{counts.info} {t('drift.sev.info')}</span>{/if}
	</div>
	{#each sorted as d, i (i)}
		<div class="dcard {sevClass(d.severity)}">
			<div class="dhead">
				<span class="sev {sevClass(d.severity)}">{t(`drift.sev.${d.severity}`)}</span>
				<span class="comp">{d.component}</span>
				<span class="name mono">{d.name}</span>
			</div>
			<p class="msg">{d.message}</p>
			{#if d.desired != null || d.observed != null}
				<div class="diff">
					<span class="cell">
						<span class="dl">{t('drift.desired')}</span>
						<code class="want">{d.desired ?? '—'}</code>
					</span>
					<span class="arrow">→</span>
					<span class="cell">
						<span class="dl">{t('drift.observed')}</span>
						<code class="got">{d.observed ?? '—'}</code>
					</span>
				</div>
			{/if}
		</div>
	{/each}
</div>

<style>
	.drift {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.summary {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-bottom: 0.15rem;
	}
	.cnt {
		font-size: 0.72rem;
		font-weight: 600;
		padding: 0.12rem 0.5rem;
		border-radius: 999px;
	}
	.cnt.crit {
		background: color-mix(in srgb, var(--bad) 15%, transparent);
		color: var(--bad);
	}
	.cnt.warn {
		background: color-mix(in srgb, var(--warn) 18%, transparent);
		color: var(--warn);
	}
	.cnt.info {
		background: color-mix(in srgb, var(--unknown) 18%, transparent);
		color: var(--unknown);
	}
	.dcard {
		border: 1px solid var(--border);
		border-left-width: 3px;
		border-radius: var(--radius-sm);
		background: var(--bg-elev);
		padding: 0.55rem 0.7rem;
	}
	.dcard.crit {
		border-left-color: var(--bad);
	}
	.dcard.warn {
		border-left-color: var(--warn);
	}
	.dcard.info {
		border-left-color: var(--unknown);
	}
	.dhead {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.sev {
		font-size: 0.66rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		padding: 0.1rem 0.42rem;
		border-radius: 999px;
	}
	.sev.crit {
		background: color-mix(in srgb, var(--bad) 16%, transparent);
		color: var(--bad);
	}
	.sev.warn {
		background: color-mix(in srgb, var(--warn) 18%, transparent);
		color: var(--warn);
	}
	.sev.info {
		background: color-mix(in srgb, var(--unknown) 18%, transparent);
		color: var(--unknown);
	}
	.comp {
		font-size: 0.78rem;
		color: var(--text-dim);
	}
	.name {
		font-size: 0.82rem;
		font-weight: 600;
		word-break: break-all;
	}
	.msg {
		margin: 0.35rem 0 0;
		font-size: 0.85rem;
		line-height: 1.45;
	}
	.diff {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		flex-wrap: wrap;
		margin-top: 0.45rem;
	}
	.cell {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.dl {
		color: var(--text-dim);
		font-size: 0.72rem;
	}
	.diff code {
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.05rem 0.4rem;
		font-size: 0.78rem;
		background: var(--bg);
	}
	.want {
		color: var(--text);
	}
	.got {
		color: var(--bad);
	}
	.arrow {
		color: var(--text-dim);
	}
</style>
