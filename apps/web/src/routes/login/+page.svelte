<script lang="ts">
	import { goto } from '$app/navigation';
	import { auth } from '$lib/auth.svelte';
	import { api, ApiError, errorMessage } from '$lib/api';
	import { defaultApiBase } from '$lib/config';
	import { toast } from '$lib/toast.svelte';
	import { t } from '$lib/i18n.svelte';
	import Logo from '$lib/components/Logo.svelte';

	let apiBase = $state(auth.apiBase || defaultApiBase());
	let token = $state('');
	let busy = $state(false);
	let error = $state('');

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		if (!token.trim()) {
			error = t('login.enterToken');
			return;
		}
		busy = true;
		// Persist first so the test call carries the credentials, then probe a
		// cheap admin endpoint to confirm the token is accepted.
		auth.login(token, apiBase);
		try {
			await api.fleetHealth();
			toast.success(t('login.signedIn'));
			await goto('/');
		} catch (err) {
			auth.logout();
			if (err instanceof ApiError && err.status === 401) {
				error = t('login.rejected');
			} else if (err instanceof ApiError && err.status === 403) {
				error = t('login.locked');
			} else {
				error = errorMessage(err);
			}
		} finally {
			busy = false;
		}
	}
</script>

<div class="wrap">
	<form class="card login" onsubmit={submit}>
		<div class="head">
			<Logo size={52} />
			<h1>{t('login.title')}</h1>
			<p class="muted">{t('login.subtitle')}</p>
		</div>

		<label class="field">
			<span>{t('login.serverUrl')}</span>
			<input bind:value={apiBase} placeholder="http://127.0.0.1:8000" autocomplete="off" />
		</label>

		<label class="field">
			<span>{t('login.adminToken')}</span>
			<!-- svelte-ignore a11y_autofocus -->
			<input
				type="password"
				bind:value={token}
				placeholder="DN42_CONTROL_ADMIN_TOKEN"
				autocomplete="off"
				autofocus
			/>
		</label>

		{#if error}
			<p class="error-text">{error}</p>
		{/if}

		<button class="btn primary" type="submit" disabled={busy} style="width:100%">
			{busy ? t('login.signingIn') : t('login.signin')}
		</button>

		<p class="hint faint">{t('login.hint')}</p>
	</form>
</div>

<style>
	.wrap {
		min-height: 100vh;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 1rem;
	}
	.login {
		width: 100%;
		max-width: 380px;
	}
	.head {
		text-align: center;
		margin-bottom: 1.25rem;
	}
	.head h1 {
		margin: 0.4rem 0 0.2rem;
		font-size: 1.3rem;
	}
	.hint {
		margin: 1rem 0 0;
		font-size: 0.75rem;
		line-height: 1.5;
	}
</style>
