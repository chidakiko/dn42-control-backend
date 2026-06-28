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
						<label class="ctl">
							<span>{t('nav.language')}</span>
							<Select
								size="sm"
								width="auto"
								value={locale.code}
								options={[
									{ value: 'en', label: 'English' },
									{ value: 'zh', label: '简体中文' }
								]}
								onChange={(v) => locale.set(v as LangCode)}
								ariaLabel={t('nav.language')}
							/>
						</label>
						<div class="api-base mono" title={auth.apiBase}>{auth.apiBase}</div>
					{/if}

					<div class="foot-actions" class:col={sidebar.collapsed}>
						<Tooltip label={THEME_LABEL[theme.pref]} enabled={sidebar.collapsed}>
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
						<Tooltip label={t('nav.signout')} enabled={sidebar.collapsed}>
							{#snippet trigger(props)}
								<button {...props} class="iconbtn" onclick={logout} aria-label={t('nav.signout')}>
									<Icon name="logout" size={16} />
								</button>
							{/snippet}
						</Tooltip>
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
				{@render children()}
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
	nav {
		display: flex;
		flex-direction: column;
		padding: 0.6rem 0.5rem;
		gap: 1px;
		flex: 1;
	}
	nav a {
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
	.collapsed nav a {
		justify-content: center;
		padding: 0.55rem 0;
	}
	nav a:hover {
		background: var(--bg-elev-2);
		color: var(--text);
		text-decoration: none;
	}
	nav a.active {
		background: var(--accent-soft);
		color: var(--accent);
	}
	/* Cloudflare-style left accent rail on the active item. */
	nav a.active::before {
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
	nav a .ic {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.15rem;
		color: var(--text-faint);
		transition: color 0.12s;
	}
	nav a:hover .ic {
		color: var(--text-dim);
	}
	nav a.active .ic {
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
		padding: 1.5rem 2rem;
		width: 100%;
		min-width: 0; /* let grid child shrink instead of overflowing */
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
