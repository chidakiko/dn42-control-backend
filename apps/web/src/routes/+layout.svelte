<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { auth } from '$lib/auth.svelte';
	import { toast } from '$lib/toast.svelte';
	import { t, locale, type LangCode } from '$lib/i18n.svelte';
	import { autoRefresh } from '$lib/refresh.svelte';
	import { theme } from '$lib/theme.svelte';
	import { sidebar } from '$lib/sidebar.svelte';
	import { Tooltip as TooltipPrimitive } from 'bits-ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Select from '$lib/components/Select.svelte';
	import Toaster from '$lib/components/Toaster.svelte';
	import Logo from '$lib/components/Logo.svelte';
	import Icon, { type IconName } from '$lib/components/Icon.svelte';

	let { children } = $props();

	const nav: { href: string; key: string; icon: IconName }[] = [
		{ href: '/', key: 'nav.dashboard', icon: 'dashboard' },
		{ href: '/nodes', key: 'nav.nodes', icon: 'nodes' },
		{ href: '/registrations', key: 'nav.registrations', icon: 'registrations' },
		{ href: '/enrollment-tokens', key: 'nav.enrollment', icon: 'tokens' },
		{ href: '/provision', key: 'nav.provision', icon: 'provision' },
		{ href: '/dns-groups', key: 'nav.dnsGroups', icon: 'dns' },
		{ href: '/audit', key: 'nav.audit', icon: 'audit' }
	];

	const THEME_ICON = { system: 'monitor', light: 'sun', dark: 'moon' } as const;
	const THEME_LABEL = { system: '跟随系统', light: '浅色', dark: '深色' } as const;

	const REFRESH_OPTS = [0, 5000, 10000, 30000, 60000];

	let isLogin = $derived(page.url.pathname === '/login');

	// Top-bar title is derived from the route so each page no longer renders its own
	// big H1 (it now lives in the shared top bar). Node detail shows the node id.
	const TITLE_KEY: Record<string, string> = {
		'/nodes': 'nodes.title',
		'/registrations': 'reg.title',
		'/enrollment-tokens': 'enr.title',
		'/provision': 'prov.title',
		'/dns-groups': 'dns.title',
		'/audit': 'audit.title'
	};
	// Breadcrumb trail for the top bar. Multi-level routes (node detail) get a
	// parent crumb + leaf; top-level routes are a single crumb (no separator).
	let crumbs: { label: string; href?: string }[] = $derived.by(() => {
		const p = page.url.pathname;
		if (p === '/') return [{ label: t('dash.title') }];
		if (p.startsWith('/nodes/')) {
			const id = decodeURIComponent(p.split('/')[2] ?? '');
			return [{ label: t('nodes.title'), href: '/nodes' }, { label: id || t('nodes.title') }];
		}
		for (const href of Object.keys(TITLE_KEY)) if (p.startsWith(href)) return [{ label: t(TITLE_KEY[href]) }];
		return [{ label: 'DN42 Control' }];
	});

	// Per-route descriptive line shown under the breadcrumb in the top bar (was each
	// page's own subtitle). Node detail keeps its data-driven identity line in the body.
	const DESC_KEY: Record<string, string> = {
		'/': 'dash.subtitle',
		'/nodes': 'nodes.subtitle',
		'/registrations': 'reg.subtitle',
		'/enrollment-tokens': 'enr.note',
		'/provision': 'prov.note',
		'/dns-groups': 'dns.subtitle',
		'/audit': 'audit.subtitle'
	};
	let pageDesc = $derived.by(() => {
		const p = page.url.pathname;
		if (p.startsWith('/nodes/')) return '';
		if (DESC_KEY[p]) return t(DESC_KEY[p]);
		for (const href of Object.keys(DESC_KEY)) if (href !== '/' && p.startsWith(href)) return t(DESC_KEY[href]);
		return '';
	});

	// Client-side route guard: bounce to /login whenever there's no token and
	// we're not already on the login screen.
	$effect(() => {
		if (!auth.isAuthed && !isLogin) {
			void goto('/login');
		}
	});

	function logout() {
		auth.logout();
		toast.info(t('common.signedOut'));
		void goto('/login');
	}

	// Global manual refresh: bump the shared tick (every live page re-fetches) with a
	// brief spin for feedback.
	let refreshing = $state(false);
	let spinTimer: ReturnType<typeof setTimeout> | null = null;
	function manualRefresh() {
		autoRefresh.refreshNow();
		refreshing = true;
		if (spinTimer) clearTimeout(spinTimer);
		spinTimer = setTimeout(() => (refreshing = false), 600);
	}

	function refreshLabel(ms: number): string {
		return ms === 0 ? t('common.off') : `${ms / 1000}s`;
	}

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname.startsWith(href);
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>DN42 Control</title>
</svelte:head>

<Toaster />

{#if isLogin || !auth.isAuthed}
	{@render children()}
{:else}
	<TooltipPrimitive.Provider delayDuration={150} disableHoverableContent>
		<div class="shell" class:collapsed={sidebar.collapsed}>
			<aside class="sidebar">
				<div class="brand">
					<a class="brandlink" href="/" aria-label="DN42 Control">
						<Logo size={28} />
						{#if !sidebar.collapsed}
							<span class="wordmark">DN42<span class="wordmark-sub">{t('app.control')}</span></span>
						{/if}
					</a>
				</div>

				<nav>
					{#each nav as item (item.href)}
						<Tooltip label={t(item.key)} enabled={sidebar.collapsed} side="right">
							{#snippet trigger(props)}
								<a
									{...props}
									href={item.href}
									class:active={isActive(item.href)}
									aria-label={t(item.key)}
								>
									<span class="ic"><Icon name={item.icon} size={18} /></span>
									{#if !sidebar.collapsed}<span class="lbl">{t(item.key)}</span>{/if}
								</a>
							{/snippet}
						</Tooltip>
					{/each}
				</nav>

				<div class="foot">
					{#if !sidebar.collapsed}
						<label class="ctl">
							<span class="inline" style="gap:0.35rem">
								{#if autoRefresh.intervalMs > 0}<span class="pulse"></span>{/if}
								{t('nav.autorefresh')}
							</span>
							<Select
								size="sm"
								width="auto"
								value={String(autoRefresh.intervalMs)}
								options={REFRESH_OPTS.map((ms) => ({ value: String(ms), label: refreshLabel(ms) }))}
								onChange={(v) => autoRefresh.set(Number(v))}
								ariaLabel={t('nav.autorefresh')}
							/>
						</label>
						<div class="api-base mono" title={auth.apiBase}>{auth.apiBase}</div>
					{/if}

					<div class="foot-actions" class:col={sidebar.collapsed}>
						<Tooltip
							label={sidebar.collapsed ? t('nav.expand') : t('nav.collapse')}
							enabled={sidebar.collapsed}
						>
							{#snippet trigger(props)}
								<button
									{...props}
									class="iconbtn collapse-btn"
									onclick={() => sidebar.toggle()}
									aria-label={sidebar.collapsed ? t('nav.expand') : t('nav.collapse')}
								>
									<Icon name="arrow-left" size={16} />
								</button>
							{/snippet}
						</Tooltip>
					</div>
				</div>
			</aside>
			<main class="content">
				<header class="topbar">
					<div class="tb-head">
						<nav class="tb-crumbs" aria-label="Breadcrumb">
							{#each crumbs as c, i (i)}
								{#if i > 0}
									<span class="tb-sep" aria-hidden="true">
										<Icon name="chevron-down" size={13} />
									</span>
								{/if}
								{#if c.href && i < crumbs.length - 1}
									<a class="tb-crumb" href={c.href}>{c.label}</a>
								{:else}
									<span class="tb-crumb current" aria-current="page">{c.label}</span>
								{/if}
							{/each}
						</nav>
						{#if pageDesc}
							<Tooltip label={pageDesc} side="bottom">
								{#snippet trigger(props)}
									<button {...props} class="tb-info" aria-label={pageDesc}>
										<Icon name="info" size={15} />
									</button>
								{/snippet}
							</Tooltip>
						{/if}
					</div>
					<div class="tb-actions">
						<Tooltip label={t('common.refresh')}>
							{#snippet trigger(props)}
								<button
									{...props}
									class="iconbtn"
									class:spin={refreshing}
									onclick={manualRefresh}
									aria-label={t('common.refresh')}
								>
									<Icon name="refresh" size={16} />
								</button>
							{/snippet}
						</Tooltip>
						<Select
							icon="languages"
							value={locale.code}
							options={[
								{ value: 'en', label: 'English' },
								{ value: 'zh', label: '简体中文' },
								{ value: 'zh-Hant', label: '繁體中文' },
								{ value: 'ja', label: '日本語' }
							]}
							onChange={(v) => locale.set(v as LangCode)}
							ariaLabel={t('nav.language')}
						/>
						<Tooltip label={THEME_LABEL[theme.pref]}>
							{#snippet trigger(props)}
								<button
									{...props}
									class="iconbtn"
									onclick={() => theme.toggle()}
									aria-label="Toggle theme"
								>
									<Icon name={THEME_ICON[theme.pref]} size={16} />
								</button>
							{/snippet}
						</Tooltip>
						<button class="btn sm tb-logout" onclick={logout}>
							<Icon name="logout" size={15} />{t('nav.signout')}
						</button>
					</div>
				</header>
				<div class="content-inner">
					{@render children()}
				</div>
			</main>
		</div>
	</TooltipPrimitive.Provider>
{/if}

<style>
	.shell {
		display: grid;
		grid-template-columns: 220px 1fr;
		min-height: 100vh;
		transition: grid-template-columns 0.18s ease;
	}
	.shell.collapsed {
		grid-template-columns: 64px 1fr;
	}
	.sidebar {
		background: var(--bg-elev);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		position: sticky;
		top: 0;
		height: 100vh;
		overflow: hidden;
	}
	.brand {
		padding: 0.9rem 1rem;
		border-bottom: 1px solid var(--border);
		height: 56px;
		display: flex;
		align-items: center;
	}
	.collapsed .brand {
		padding: 0.9rem 0;
		justify-content: center;
	}
	.brandlink {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		color: inherit;
	}
	.brandlink:hover {
		text-decoration: none;
	}
	.wordmark {
		display: flex;
		flex-direction: column;
		line-height: 1.1;
		font-weight: 700;
		font-size: 0.95rem;
		letter-spacing: -0.01em;
	}
	.wordmark-sub {
		font-weight: 500;
		font-size: 0.68rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-faint);
	}
	.sidebar nav {
		display: flex;
		flex-direction: column;
		padding: 0.6rem 0.5rem;
		gap: 1px;
		flex: 1;
	}
	.sidebar nav a {
		position: relative;
		display: flex;
		align-items: center;
		gap: 0.65rem;
		padding: 0.5rem 0.7rem;
		border-radius: var(--radius-sm);
		color: var(--text-dim);
		font-size: 0.9rem;
		font-weight: 500;
		transition: background 0.12s, color 0.12s;
	}
	.collapsed .sidebar nav a {
		justify-content: center;
		padding: 0.55rem 0;
	}
	.sidebar nav a:hover {
		background: var(--bg-elev-2);
		color: var(--text);
		text-decoration: none;
	}
	.sidebar nav a.active {
		background: var(--accent-soft);
		color: var(--accent);
	}
	/* Cloudflare-style left accent rail on the active item. */
	.sidebar nav a.active::before {
		content: '';
		position: absolute;
		left: -0.5rem;
		top: 50%;
		transform: translateY(-50%);
		width: 3px;
		height: 18px;
		border-radius: 0 3px 3px 0;
		background: var(--accent);
	}
	.sidebar nav a .ic {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.15rem;
		color: var(--text-faint);
		transition: color 0.12s;
	}
	.sidebar nav a:hover .ic {
		color: var(--text-dim);
	}
	.sidebar nav a.active .ic {
		color: var(--accent);
	}
	.foot {
		padding: 0.75rem;
		border-top: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.collapsed .foot {
		padding: 0.6rem 0;
		align-items: center;
	}
	.ctl {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
	}
	.ctl > span {
		color: var(--text-faint);
		font-size: 0.75rem;
	}
	.foot-actions {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		margin-top: 0.15rem;
	}
	.foot-actions.col {
		flex-direction: column;
	}
	.iconbtn {
		background: transparent;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		color: var(--text-dim);
		cursor: pointer;
		width: 30px;
		height: 30px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0;
	}
	.iconbtn:hover {
		color: var(--text);
		border-color: var(--text-faint);
		background: var(--bg-elev-2);
	}
	/* the collapse chevron points right (→) once collapsed */
	.collapse-btn {
		margin-left: auto;
		transition: transform 0.18s ease;
	}
	.collapsed .collapse-btn {
		margin-left: 0;
		transform: rotate(180deg);
	}
	.pulse {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--ok);
		box-shadow: 0 0 0 0 rgba(46, 160, 67, 0.6);
		animation: pulse 2s infinite;
		flex: none;
	}
	@keyframes pulse {
		0% {
			box-shadow: 0 0 0 0 rgba(46, 160, 67, 0.5);
		}
		70% {
			box-shadow: 0 0 0 6px rgba(46, 160, 67, 0);
		}
		100% {
			box-shadow: 0 0 0 0 rgba(46, 160, 67, 0);
		}
	}
	.api-base {
		font-size: 0.7rem;
		color: var(--text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.content {
		width: 100%;
		min-width: 0; /* let grid child shrink instead of overflowing */
		display: flex;
		flex-direction: column;
	}
	/* shared top bar: page title (left) + global controls (right); aligns with the
	   sidebar brand (both 56px) for one continuous header line. */
	.topbar {
		position: sticky;
		top: 0;
		z-index: 20;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		min-height: 56px;
		padding: 0.5rem 2rem;
		background: color-mix(in srgb, var(--bg-elev) 88%, transparent);
		backdrop-filter: blur(8px);
		border-bottom: 1px solid var(--border);
	}
	/* left side: breadcrumb + a small info icon whose tooltip holds the page description */
	.tb-head {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-width: 0;
	}
	.tb-info {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: none;
		padding: 2px;
		color: var(--text-faint);
		cursor: help;
		flex: none;
	}
	.tb-info:hover {
		color: var(--text-dim);
	}
	.tb-crumbs {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		min-width: 0;
		overflow: hidden;
	}
	.tb-crumb {
		font-size: 1.15rem;
		font-weight: 700;
		letter-spacing: -0.01em;
		white-space: nowrap;
		text-overflow: ellipsis;
		overflow: hidden;
	}
	/* parent crumbs: lighter + linkable; leaf: full-strength current page */
	a.tb-crumb {
		color: var(--text-dim);
		font-weight: 600;
	}
	a.tb-crumb:hover {
		color: var(--text);
		text-decoration: none;
	}
	.tb-crumb.current {
		color: var(--text);
	}
	.tb-sep {
		display: inline-flex;
		align-items: center;
		color: var(--text-faint);
		transform: rotate(-90deg);
		flex: none;
	}
	.tb-actions {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.iconbtn.spin :global(svg) {
		animation: tb-spin 0.6s ease;
	}
	@keyframes tb-spin {
		from {
			transform: rotate(0);
		}
		to {
			transform: rotate(360deg);
		}
	}
	.tb-logout {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
	.content-inner {
		padding: 1.5rem 2rem;
		min-width: 0;
	}
	@media (max-width: 720px) {
		.shell,
		.shell.collapsed {
			grid-template-columns: 1fr;
		}
		.sidebar {
			position: static;
			height: auto;
		}
	}
</style>
