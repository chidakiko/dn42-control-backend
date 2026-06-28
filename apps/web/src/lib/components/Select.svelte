<script lang="ts">
	// Reusable themed single-select over Bits UI Select — replaces native <select> with
	// a stylable, accessible, portaled dropdown that matches the app's look. String
	// values only; numeric callers convert at the call site.
	import { Select } from 'bits-ui';
	import Icon, { type IconName } from './Icon.svelte';

	interface Opt {
		value: string;
		label: string;
		disabled?: boolean;
	}
	let {
		value = $bindable(),
		options,
		placeholder = '',
		ariaLabel,
		onChange,
		size = 'md',
		width,
		disabled = false,
		icon
	}: {
		value: string;
		options: Opt[];
		placeholder?: string;
		ariaLabel?: string;
		onChange?: (v: string) => void;
		size?: 'sm' | 'md';
		/** trigger width override (e.g. "auto", "12rem"); defaults to 100% */
		width?: string;
		disabled?: boolean;
		/** when set, the trigger is a compact icon button (hides value text + caret) */
		icon?: IconName;
	} = $props();

	let selectedLabel = $derived(options.find((o) => o.value === value)?.label ?? '');
</script>

<Select.Root
	type="single"
	bind:value
	items={options}
	{disabled}
	onValueChange={(v) => onChange?.(v)}
>
	<Select.Trigger
		class="sel-trigger {size} {icon ? 'icononly' : ''}"
		aria-label={ariaLabel}
		title={icon ? ariaLabel : undefined}
		style={icon ? undefined : width ? `width:${width}` : undefined}
	>
		{#if icon}
			<Icon name={icon} size={16} />
		{:else}
			<span class="sel-val" class:placeholder={!selectedLabel}>{selectedLabel || placeholder}</span>
			<span class="sel-caret"><Icon name="chevron-down" size={14} /></span>
		{/if}
	</Select.Trigger>
	<Select.Portal>
		<Select.Content class="sel-content" sideOffset={6}>
			<Select.Viewport>
				{#each options as o (o.value)}
					<Select.Item class="sel-item" value={o.value} label={o.label} disabled={o.disabled}>
						{#snippet children({ selected })}
							<span class="sel-item-label">{o.label}</span>
							{#if selected}<span class="sel-check">✓</span>{/if}
						{/snippet}
					</Select.Item>
				{/each}
			</Select.Viewport>
		</Select.Content>
	</Select.Portal>
</Select.Root>

<style>
	:global(.sel-trigger) {
		display: inline-flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.4rem;
		background: var(--bg-elev);
		border: 1px solid var(--border-strong);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 0.875rem;
		padding: 0.45rem 0.6rem;
		cursor: pointer;
		width: 100%;
		min-width: 6rem;
		text-align: left;
	}
	:global(.sel-trigger.sm) {
		font-size: 0.78rem;
		padding: 0.25rem 0.5rem;
		min-width: 5rem;
	}
	/* compact icon-only trigger (e.g. language switcher): matches the topbar .iconbtn
	   exactly (30px square, transparent, --border) so it sits flush with the others. */
	:global(.sel-trigger.icononly) {
		width: 30px;
		height: 30px;
		min-width: 0;
		padding: 0;
		justify-content: center;
		background: transparent;
		border-color: var(--border);
		color: var(--text-dim);
	}
	:global(.sel-trigger.icononly:hover) {
		color: var(--text);
		border-color: var(--text-faint);
		background: var(--bg-elev-2);
	}
	:global(.sel-trigger:hover) {
		border-color: var(--text-faint);
	}
	:global(.sel-trigger[data-state='open']) {
		border-color: var(--accent);
		box-shadow: var(--ring);
	}
	:global(.sel-trigger[data-disabled]) {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.sel-val.placeholder {
		color: var(--text-faint);
	}
	.sel-caret {
		display: inline-flex;
		color: var(--text-faint);
	}
	:global(.sel-content) {
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		box-shadow: var(--shadow);
		padding: 0.25rem;
		z-index: 700;
		max-height: 18rem;
		overflow-y: auto;
		min-width: var(--bits-select-anchor-width, 8rem);
	}
	:global(.sel-item) {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
		padding: 0.35rem 0.55rem;
		border-radius: var(--radius-sm);
		font-size: 0.85rem;
		color: var(--text-dim);
		cursor: pointer;
		user-select: none;
	}
	:global(.sel-item[data-highlighted]) {
		background: var(--bg-elev-2);
		color: var(--text);
	}
	:global(.sel-item[data-selected]) {
		color: var(--accent);
	}
	:global(.sel-item[data-disabled]) {
		opacity: 0.45;
		pointer-events: none;
	}
	.sel-check {
		color: var(--accent);
	}
</style>
