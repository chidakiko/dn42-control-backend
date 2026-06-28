// Thin typed fetch wrapper around the control server admin API.
//
// Every call injects the admin Bearer token from `auth`. A 401 means the token
// is gone/invalid, so we drop it and let the route guard bounce to /login.
// Errors are normalised into `ApiError` carrying the parsed FastAPI `detail`.

import { goto } from '$app/navigation';
import { auth } from './auth.svelte';
import type {
	AgentTokenOut,
	AuditEntry,
	DnsGroupOut,
	DnsGroupZoneOut,
	DnsRecordOut,
	EnrollmentTokenCreated,
	EnrollmentTokenOut,
	FleetHealth,
	FleetOverview,
	FleetRoutingOverview,
	FleetTraffic,
	FleetTrafficBreakdown,
	NodeTraffic,
	NodeLinks,
	NodeBgpSessions,
	NodeOverview,
	GenerationDetailOut,
	GenerationDiffOut,
	GenerationOut,
	InterfaceOut,
	InternalTopologyView,
	NodeHealthDetail,
	NodeIn,
	NodeOut,
	NodePatch,
	NodeStatusEvents,
	NotifyResponse,
	PeeringIn,
	PeeringOut,
	ProvisionOut,
	Registration,
	RoutingDashboard,
	RoutingPrefixes,
	SessionOut
} from './types';

export class ApiError extends Error {
	status: number;
	detail: unknown;
	constructor(status: number, message: string, detail: unknown) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
	}
}

/** Turn a FastAPI error body into a single human-readable line. */
export function errorMessage(err: unknown): string {
	if (err instanceof ApiError) {
		const d = err.detail as
			| { message?: string; detail?: unknown }
			| string
			| undefined;
		if (typeof d === 'string') return d;
		if (d && typeof d === 'object') {
			if (typeof (d as { message?: string }).message === 'string') {
				return (d as { message: string }).message;
			}
			return JSON.stringify(d);
		}
		return err.message;
	}
	if (err instanceof Error) return err.message;
	return String(err);
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

async function request<T>(
	method: Method,
	path: string,
	body?: unknown
): Promise<T> {
	const headers: Record<string, string> = {};
	if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`;
	if (body !== undefined) headers['Content-Type'] = 'application/json';

	let res: Response;
	try {
		res = await fetch(`${auth.apiBase}${path}`, {
			method,
			headers,
			body: body === undefined ? undefined : JSON.stringify(body)
		});
	} catch {
		throw new ApiError(
			0,
			`Cannot reach control server at ${auth.apiBase}. Is it running and is CORS enabled?`,
			null
		);
	}

	if (res.status === 401) {
		auth.logout();
		// Avoid a redirect loop if we're already heading to login.
		void goto('/login');
		throw new ApiError(401, 'Unauthorized — admin token rejected', null);
	}

	if (res.status === 204) return undefined as T;

	let payload: unknown = null;
	const text = await res.text();
	if (text) {
		try {
			payload = JSON.parse(text);
		} catch {
			payload = text;
		}
	}

	if (!res.ok) {
		const detail =
			payload && typeof payload === 'object' && 'detail' in payload
				? (payload as { detail: unknown }).detail
				: payload;
		throw new ApiError(res.status, `HTTP ${res.status} ${res.statusText}`, detail);
	}

	return payload as T;
}

export const api = {
	get: <T>(path: string) => request<T>('GET', path),
	post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
	patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
	del: (path: string) => request<void>('DELETE', path),

	// --- Health / fleet (general admin reads) ---
	fleetHealth: () => request<FleetHealth>('GET', '/api/v1/admin/health'),
	nodeHealth: (id: string) =>
		request<NodeHealthDetail>('GET', `/api/v1/admin/nodes/${enc(id)}/health`),
	statusEvents: (id: string, kind?: string, limit = 50) =>
		request<NodeStatusEvents>(
			'GET',
			`/api/v1/admin/nodes/${enc(id)}/status-events?limit=${limit}` +
				(kind ? `&kind=${kind}` : '')
		),

	// --- WebUI-specific BFF / aggregation endpoints (/api/v1/ui, server pre-computes) ---
	fleetOverview: () => request<FleetOverview>('GET', '/api/v1/ui/fleet/overview'),
	fleetTraffic: (limit = 120) =>
		request<FleetTraffic>('GET', `/api/v1/ui/fleet/traffic?limit=${limit}`),
	fleetTrafficBreakdown: () =>
		request<FleetTrafficBreakdown>('GET', '/api/v1/ui/fleet/traffic-breakdown'),
	nodeTraffic: (id: string, limit = 120) =>
		request<NodeTraffic>('GET', `/api/v1/ui/nodes/${enc(id)}/traffic?limit=${limit}`),
	nodeLinks: (id: string) =>
		request<NodeLinks>('GET', `/api/v1/ui/nodes/${enc(id)}/links`),
	nodeBgpSessions: (id: string) =>
		request<NodeBgpSessions>('GET', `/api/v1/ui/nodes/${enc(id)}/bgp-sessions/status`),
	nodeOverview: (id: string) =>
		request<NodeOverview>('GET', `/api/v1/ui/nodes/${enc(id)}/overview`),
	// Dashboard routing board: summary + nodes + server-aggregated trend.
	routingFleetOverview: () =>
		request<FleetRoutingOverview>('GET', '/api/v1/ui/routing/fleet-overview'),
	// RoutingTab 头部一次取全（summary + origins + timeline），取代 3 次跨网往返。
	routingDashboard: (id: string, originsLimit = 15, timelineLimit = 200) =>
		request<RoutingDashboard>(
			'GET',
			`/api/v1/ui/nodes/${enc(id)}/routing/dashboard` +
				`?origins_limit=${originsLimit}&timeline_limit=${timelineLimit}`
		),

	// --- Routing table (general full-table analysis, admin) ---
	routingPrefixes: (
		id: string,
		opts: {
			family?: '4' | '6';
			scope?: 'all' | 'local' | 'external';
			q?: string;
			limit?: number;
			offset?: number;
		} = {}
	) => {
		const p = new URLSearchParams();
		if (opts.family) p.set('family', opts.family);
		if (opts.scope && opts.scope !== 'all') p.set('scope', opts.scope);
		if (opts.q) p.set('q', opts.q);
		p.set('limit', String(opts.limit ?? 100));
		p.set('offset', String(opts.offset ?? 0));
		return request<RoutingPrefixes>(
			'GET',
			`/api/v1/admin/nodes/${enc(id)}/routing/prefixes?${p.toString()}`
		);
	},

	// iBGP + OSPF internal interconnect (not bgp_sessions records — synthesised
	// from bird.internal_topology; correlated with routing peers for liveness).
	nodeInternalTopology: (id: string) =>
		request<InternalTopologyView>(
			'GET',
			`/api/v1/ui/nodes/${enc(id)}/internal-topology`
		),

	// --- Nodes ---
	listNodes: () => request<NodeOut[]>('GET', '/api/v1/admin/nodes'),
	getNode: (id: string) => request<NodeOut>('GET', `/api/v1/admin/nodes/${enc(id)}`),
	createNode: (body: NodeIn) => request<NodeOut>('POST', '/api/v1/admin/nodes', body),
	updateNode: (id: string, body: NodePatch) =>
		request<NodeOut>('PATCH', `/api/v1/admin/nodes/${enc(id)}`, body),
	deleteNode: (id: string) => request<void>('DELETE', `/api/v1/admin/nodes/${enc(id)}`),
	decommissionNode: (id: string) =>
		request<NodeOut>('POST', `/api/v1/admin/nodes/${enc(id)}/decommission`),
	recommissionNode: (id: string) =>
		request<NodeOut>('POST', `/api/v1/admin/nodes/${enc(id)}/recommission`),
	nodeDesiredState: (id: string) =>
		request<Record<string, unknown>>(
			'GET',
			`/api/v1/admin/nodes/${enc(id)}/desired-state`
		),
	notifyNode: (id: string, event: string, reason?: string) =>
		request<NotifyResponse>('POST', `/api/v1/admin/nodes/${enc(id)}/notify`, {
			event,
			reason
		}),

	// --- Generations ---
	listGenerations: (id: string, limit = 50) =>
		request<GenerationOut[]>(
			'GET',
			`/api/v1/admin/nodes/${enc(id)}/generations?limit=${limit}`
		),
	getGeneration: (id: string, gen: number) =>
		request<GenerationDetailOut>(
			'GET',
			`/api/v1/admin/nodes/${enc(id)}/generations/${gen}`
		),
	diffGeneration: (id: string, gen: number, against?: number) =>
		request<GenerationDiffOut>(
			'GET',
			`/api/v1/admin/nodes/${enc(id)}/generations/${gen}/diff` +
				(against !== undefined ? `?against=${against}` : '')
		),
	rollbackGeneration: (id: string, gen: number, reason?: string) =>
		request<unknown>(
			'POST',
			`/api/v1/admin/nodes/${enc(id)}/generations/${gen}/rollback`,
			{ reason }
		),

	// --- Peerings ---
	listPeerings: (id: string) =>
		request<PeeringOut[]>('GET', `/api/v1/admin/nodes/${enc(id)}/peerings`),
	createPeering: (id: string, body: PeeringIn) =>
		request<PeeringOut>('POST', `/api/v1/admin/nodes/${enc(id)}/peerings`, body),
	updatePeering: (pid: number, body: Partial<PeeringIn>) =>
		request<PeeringOut>('PATCH', `/api/v1/admin/peerings/${pid}`, body),
	deletePeering: (pid: number) =>
		request<void>('DELETE', `/api/v1/admin/peerings/${pid}`),
	provisionPeering: (id: string, body: unknown) =>
		request<unknown>('POST', `/api/v1/admin/nodes/${enc(id)}/peerings/provision`, body),

	// --- Interfaces ---
	listInterfaces: (id: string) =>
		request<InterfaceOut[]>('GET', `/api/v1/admin/nodes/${enc(id)}/interfaces`),
	createInterface: (id: string, body: unknown) =>
		request<InterfaceOut>('POST', `/api/v1/admin/nodes/${enc(id)}/interfaces`, body),
	updateInterface: (iid: number, body: unknown) =>
		request<InterfaceOut>('PATCH', `/api/v1/admin/interfaces/${iid}`, body),
	deleteInterface: (iid: number) =>
		request<void>('DELETE', `/api/v1/admin/interfaces/${iid}`),

	// --- BGP sessions ---
	listSessions: (id: string) =>
		request<SessionOut[]>('GET', `/api/v1/admin/nodes/${enc(id)}/bgp-sessions`),
	createSession: (id: string, body: unknown) =>
		request<SessionOut>('POST', `/api/v1/admin/nodes/${enc(id)}/bgp-sessions`, body),
	updateSession: (sid: number, body: unknown) =>
		request<SessionOut>('PATCH', `/api/v1/admin/bgp-sessions/${sid}`, body),
	deleteSession: (sid: number) =>
		request<void>('DELETE', `/api/v1/admin/bgp-sessions/${sid}`),

	// --- DNS groups（共享 / anycast）---
	listDnsGroups: () => request<DnsGroupOut[]>('GET', `/api/v1/admin/dns-groups`),
	createDnsGroup: (body: unknown) =>
		request<DnsGroupOut>('POST', `/api/v1/admin/dns-groups`, body),
	updateDnsGroup: (gid: number, body: unknown) =>
		request<DnsGroupOut>('PATCH', `/api/v1/admin/dns-groups/${gid}`, body),
	deleteDnsGroup: (gid: number) => request<void>('DELETE', `/api/v1/admin/dns-groups/${gid}`),
	// 组声明的权威 zone（zone 名 + 可选 SOA 覆盖）。
	listGroupZones: (gid: number) =>
		request<DnsGroupZoneOut[]>('GET', `/api/v1/admin/dns-groups/${gid}/zones`),
	createGroupZone: (gid: number, body: unknown) =>
		request<DnsGroupZoneOut>('POST', `/api/v1/admin/dns-groups/${gid}/zones`, body),
	updateGroupZone: (gid: number, zid: number, body: unknown) =>
		request<DnsGroupZoneOut>('PATCH', `/api/v1/admin/dns-groups/${gid}/zones/${zid}`, body),
	deleteGroupZone: (gid: number, zid: number) =>
		request<void>('DELETE', `/api/v1/admin/dns-groups/${gid}/zones/${zid}`),
	// 扁平记录（name/type/content/ttl/comment），归属某个 zone。
	listZoneRecords: (gid: number, zid: number) =>
		request<DnsRecordOut[]>('GET', `/api/v1/admin/dns-groups/${gid}/zones/${zid}/records`),
	createZoneRecord: (gid: number, zid: number, body: unknown) =>
		request<DnsRecordOut>('POST', `/api/v1/admin/dns-groups/${gid}/zones/${zid}/records`, body),
	updateZoneRecord: (gid: number, zid: number, rid: number, body: unknown) =>
		request<DnsRecordOut>(
			'PATCH',
			`/api/v1/admin/dns-groups/${gid}/zones/${zid}/records/${rid}`,
			body
		),
	deleteZoneRecord: (gid: number, zid: number, rid: number) =>
		request<void>('DELETE', `/api/v1/admin/dns-groups/${gid}/zones/${zid}/records/${rid}`),
	// 给节点分配 / 取消（null）DNS 组。
	assignNodeDnsGroup: (id: string, dnsGroupId: number | null) =>
		request<DnsGroupOut | null>('PUT', `/api/v1/admin/nodes/${enc(id)}/dns-group`, {
			dns_group_id: dnsGroupId
		}),

	// --- Agent tokens ---
	listAgentTokens: (id: string) =>
		request<AgentTokenOut[]>('GET', `/api/v1/admin/nodes/${enc(id)}/agent-tokens`),
	issueAgentToken: (id: string, body: unknown) =>
		request<AgentTokenOut>('POST', `/api/v1/admin/nodes/${enc(id)}/agent-tokens`, body),
	rotateAgentToken: (tid: string, body?: unknown) =>
		request<AgentTokenOut>('POST', `/api/v1/admin/agent-tokens/${enc(tid)}/rotate`, body),
	revokeAgentToken: (tid: string) =>
		request<void>('DELETE', `/api/v1/admin/agent-tokens/${enc(tid)}`),

	// --- Enrollment tokens ---
	listEnrollmentTokens: () =>
		request<EnrollmentTokenOut[]>('GET', '/api/v1/admin/enrollment-tokens'),
	createEnrollmentToken: (body: unknown) =>
		request<EnrollmentTokenCreated>('POST', '/api/v1/admin/enrollment-tokens', body),
	deleteEnrollmentToken: (tid: string) =>
		request<void>('DELETE', `/api/v1/admin/enrollment-tokens/${enc(tid)}`),

	// --- Registrations ---
	listRegistrations: (status?: string) =>
		request<{ registrations: Registration[] }>(
			'GET',
			'/api/v1/admin/registrations' + (status ? `?status=${status}` : '')
		),
	approveRegistration: (rid: number, note?: string) =>
		request<Registration>('POST', `/api/v1/admin/registrations/${rid}/approve`, {
			note
		}),
	rejectRegistration: (rid: number, note?: string) =>
		request<Registration>('POST', `/api/v1/admin/registrations/${rid}/reject`, {
			note
		}),

	// --- Provision + audit ---
	provision: (body: unknown) =>
		request<ProvisionOut>('POST', '/api/v1/admin/provision', body),
	auditLog: (limit = 100) =>
		request<{ entries: AuditEntry[] }>('GET', `/api/v1/admin/audit-log?limit=${limit}`)
};

function enc(segment: string): string {
	return encodeURIComponent(segment);
}
