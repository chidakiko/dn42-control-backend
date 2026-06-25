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
	<div class="shell">
		<aside class="sidebar">
			<div class="brand">
				<Logo size={30} />
				<span class="wordmark">DN42<span class="wordmark-sub">{t('app.control')}</span></span>
				<button
					class="theme-toggle"
					onclick={() => theme.toggle()}
					title={THEME_LABEL[theme.pref]}
					aria-label="Toggle theme"
				>
					<Icon name={THEME_ICON[theme.pref]} size={16} />
				</button>
			</div>
			<nav>
				{#each nav as item (item.href)}
					<a href={item.href} class:active={isActive(item.href)}>
						<span class="ic"><Icon name={item.icon} size={18} /></span>{t(item.key)}
					</a>
				{/each}
			</nav>
			<div class="foot">
				<label class="ctl">
					<span class="inline" style="gap:0.35rem">
						{#if autoRefresh.intervalMs > 0}<span class="pulse"></span>{/if}
						{t('nav.autorefresh')}
					</span>
					<select
						value={autoRefresh.intervalMs}
						onchange={(e) => autoRefresh.set(Number(e.currentTarget.value))}
					>
						{#each REFRESH_OPTS as ms (ms)}
							<option value={ms}>{refreshLabel(ms)}</option>
						{/each}
					</select>
				</label>
				<label class="ctl">
					<span>{t('nav.language')}</span>
					<select
						value={locale.code}
						onchange={(e) => locale.set(e.currentTarget.value as LangCode)}
					>
						<option value="en">English</option>
						<option value="zh">简体中文</option>
					</select>
				</label>
				<div class="api-base mono" title={auth.apiBase}>{auth.apiBase}</div>
				<button class="btn ghost sm" onclick={logout}>
					<Icon name="logout" size={15} />{t('nav.signout')}
				</button>
			</div>
		</aside>
		<main class="content">
			{@render children()}
		</main>
	</div>
{/if}

<style>
	.shell {
		display: grid;
		grid-template-columns: 220px 1fr;
		min-height: 100vh;
	}
	.sidebar {
		background: var(--bg-elev);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		position: sticky;
		top: 0;
		height: 100vh;
	}
	.brand {
		padding: 0.9rem 1rem;
		display: flex;
		align-items: center;
		gap: 0.55rem;
		border-bottom: 1px solid var(--border);
		height: 56px;
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
	.theme-toggle {
		margin-left: auto;
		align-self: center;
		background: transparent;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		color: var(--text-dim);
		cursor: pointer;
		width: 28px;
		height: 28px;
		font-size: 0.95rem;
		line-height: 1;
		padding: 0;
	}
	.theme-toggle:hover {
		color: var(--text);
		border-color: var(--text-faint);
		background: var(--bg-elev-2);
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
	.ctl select {
		width: auto;
		padding: 0.2rem 0.4rem;
		font-size: 0.78rem;
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
		max-width: 1200px;
		width: 100%;
	}
	@media (max-width: 720px) {
		.shell {
			grid-template-columns: 1fr;
		}
		.sidebar {
			position: static;
			height: auto;
		}
	}
</style>
