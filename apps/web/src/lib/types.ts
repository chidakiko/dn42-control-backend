// TypeScript mirrors of the control server's admin DTOs.
// Source of truth: apps/control-server/app/api/v1/admin/*.py and schemas/health.py.

export type NodeHealthValue = 'ok' | 'stale' | 'degraded' | 'down' | 'unknown';

export interface NodeOut {
	node_id: string;
	asn: number;
	router_id: string;
	site: string | null;
	loopback_ipv4: string | null;
	loopback_ipv6: string | null;
	link_local: string | null;
	ipv4_prefixes: string[];
	ipv6_prefixes: string[];
	inventory: Record<string, unknown>;
	labels: Record<string, string>;
	base_template: Record<string, unknown>;
	current_generation: number;
	lifecycle: string;
	dns_group_id: number | null;
	created_at: string;
	updated_at: string;
}

export interface NodeIn {
	node_id: string;
	asn: number;
	router_id: string;
	site?: string | null;
	loopback_ipv4?: string | null;
	loopback_ipv6?: string | null;
	link_local?: string | null;
	ipv4_prefixes?: string[];
	ipv6_prefixes?: string[];
	inventory?: Record<string, unknown>;
	labels?: Record<string, string>;
	base_template?: Record<string, unknown>;
}

export type NodePatch = Partial<Omit<NodeIn, 'node_id'>>;

export interface NodeHealthRow {
	node_id: string;
	health: NodeHealthValue;
	desired_generation: number | null;
	observed_generation: number | null;
	last_report_status: string | null;
	last_apply_status: string | null;
	drift_count: number;
	last_snapshot_at: string | null;
	last_report_at: string | null;
	last_apply_at: string | null;
	updated_at: string | null;
}

export interface FleetHealth {
	summary: Partial<Record<NodeHealthValue, number>>;
	nodes: NodeHealthRow[];
}

export interface NodeHealthDetail extends NodeHealthRow {
	last_snapshot: Record<string, unknown> | null;
	last_report: Record<string, unknown> | null;
	last_apply: Record<string, unknown> | null;
}

export interface StatusEvent {
	id: number;
	kind: string;
	generation: number | null;
	status: string | null;
	created_at: string | null;
	payload: Record<string, unknown>;
}

// agent 进程自观测，随 RuntimeSnapshot 上报、落在 last_snapshot.self_metrics。
// 全可选——旧 agent / 尚未采到时为 null。镜像 dn42_schemas.AgentSelfMetrics。
export interface AgentSelfMetrics {
	cpu_percent: number | null;
	rss_mb: number | null;
	last_routing_collect_seconds: number | null;
	last_reresolve_seconds: number | null;
	last_reconcile_duration_seconds: number | null;
	total_reconciles: number | null;
	total_failures: number | null;
	consecutive_failures: number | null;
	self_observed_at: string | null;
	last_reconcile_at: string | null;
}

export interface NodeStatusEvents {
	node_id: string;
	events: StatusEvent[];
}

export interface GenerationOut {
	generation: number;
	reason: string | null;
	published_at: string;
}

export interface GenerationDetailOut extends GenerationOut {
	snapshot: Record<string, unknown>;
}

export interface GenerationDiffOut {
	node_id: string;
	from_generation: number;
	to_generation: number;
	changed: boolean;
	changes: Array<Record<string, unknown>>;
}

export interface PeeringOut {
	id: number;
	local_node_id: string;
	remote_node_id: string | null;
	name: string;
	remote_asn: number;
	remote_label: string | null;
	is_internal: boolean;
	enabled: boolean;
	notes: string | null;
	created_at: string;
	updated_at: string;
}

export interface PeeringIn {
	name: string;
	remote_asn: number;
	remote_node_id?: string | null;
	remote_label?: string | null;
	is_internal?: boolean;
	enabled?: boolean;
	notes?: string | null;
}

export interface InterfaceOut {
	id: number;
	node_id: string;
	peering_id: number | null;
	name: string;
	kind: string;
	enabled: boolean;
	sort_order: number;
	spec: Record<string, unknown>;
}

export interface SessionOut {
	id: number;
	node_id: string;
	peering_id: number | null;
	name: string;
	remote_asn: number;
	enabled: boolean;
	sort_order: number;
	spec: Record<string, unknown>;
}

export interface DnsGroupOut {
	id: number;
	name: string;
	bind_addresses: string[];
	cache_ttl_seconds: number;
	forwards: Record<string, unknown>[];
	enabled: boolean;
	zone_count: number;
	member_count: number;
	created_at: string;
	updated_at: string;
}

export interface DnsGroupZoneOut {
	id: number;
	dns_group_id: number;
	zone: string;
	primary_ns: string | null;
	admin_email: string | null;
	soa_refresh: number | null;
	soa_retry: number | null;
	soa_expire: number | null;
	soa_minimum: number | null;
	default_ttl: number | null;
	enabled: boolean;
	record_count: number;
	created_at: string;
	updated_at: string;
}

export interface DnsRecordOut {
	id: number;
	dns_group_zone_id: number;
	name: string;
	type: string;
	content: string;
	ttl: number | null;
	comment: string | null;
	enabled: boolean;
	sort_order: number;
	created_at: string;
	updated_at: string;
}

export interface AgentTokenOut {
	token: string;
	secret: string | null;
	node_id: string;
	agent_id: string;
	issued_at: string;
	expires_at: string | null;
	revoked_at: string | null;
}

export interface EnrollmentTokenOut {
	token_id: string;
	node_id: string | null;
	description: string | null;
	expires_at: string | null;
	used_at: string | null;
	created_at: string;
}

export interface EnrollmentTokenCreated extends EnrollmentTokenOut {
	secret: string;
}

export interface Registration {
	id: number;
	node_id: string;
	agent_id?: string | null;
	status: 'pending' | 'approved' | 'rejected';
	inventory?: Record<string, unknown>;
	note?: string | null;
	created_at?: string | null;
	updated_at?: string | null;
	[key: string]: unknown;
}

export interface AuditEntry {
	id: number;
	actor: string | null;
	method: string;
	path: string;
	status_code: number;
	detail: Record<string, unknown>;
	created_at?: string | null;
	[key: string]: unknown;
}

// --- Routing table (Radar-style full-table analysis) ---

export interface RpkiCounts {
	valid: number;
	invalid: number;
	not_found: number;
}

export interface RouteEntry {
	prefix: string;
	origin_asn: number | null;
	as_path: number[];
	next_hop: string | null;
	protocol: string | null;
	primary: boolean;
	local: boolean;
	communities: string[];
	large_communities: string[];
	rpki: string | null;
}

export interface RoutingSummary {
	node_id: string;
	observation: string;
	captured_at: string | null;
	updated_at: string | null;
	route_count: number;
	route_count_v4: number;
	route_count_v6: number;
	local_count: number;
	rpki: RpkiCounts;
	rpki_observed: boolean;
	// { "4": { "24": n, ... }, "6": { "48": n, ... } }
	prefix_lengths: Record<string, Record<string, number>>;
	// { "<as_path_len>": n }
	as_path_lengths: Record<string, number>;
	peers: Array<{ protocol: string; count: number }>;
	prefilter: PrefilterRpki | null;
}

export interface PrefilterPeer {
	protocol: string;
	remote_asn: number | null;
	received: number;
	accepted: number;
	valid: number;
	invalid: number;
	not_found: number;
}

export interface PrefilterRoute {
	prefix: string;
	origin_asn: number | null;
	protocol: string;
	// 仅 filtered_routes：out_of_range | self_net | as_path_too_long | blocked_asn | policy
	reason?: string | null;
}

export interface PrefilterRpki {
	received: number;
	accepted: number;
	valid: number;
	invalid: number;
	not_found: number;
	peers: PrefilterPeer[];
	invalid_routes: PrefilterRoute[];
	filtered_routes: PrefilterRoute[];
}

export interface RoutingOrigins {
	node_id: string;
	total: number;
	origins: Array<{ asn: number; count: number }>;
}

export interface RoutingPrefixes {
	node_id: string;
	total: number;
	limit: number;
	offset: number;
	routes: RouteEntry[];
}

export interface RoutingTimelineEvent {
	id: number;
	captured_at: string | null;
	created_at: string | null;
	route_count: number;
	route_count_v4: number;
	route_count_v6: number;
	rpki: RpkiCounts;
	announced: number;
	withdrawn: number;
}

export interface RoutingTimeline {
	node_id: string;
	events: RoutingTimelineEvent[];
}

export interface FleetRoutingNode {
	node_id: string;
	observation: string;
	captured_at: string | null;
	route_count: number;
	route_count_v4: number;
	route_count_v6: number;
	rpki: RpkiCounts;
}

export interface FleetRouting {
	summary: {
		route_count: number;
		route_count_v4: number;
		route_count_v6: number;
		rpki: RpkiCounts;
		nodes_reporting: number;
	};
	nodes: FleetRoutingNode[];
}

// iBGP/OSPF are not bgp_sessions records — they are synthesised from
// bird.internal_topology. This view (GET /admin/nodes/{id}/internal-topology)
// exposes that config plus a routing-derived liveness hint (rib_routes).
export interface IbgpPeerView {
	node: string;
	ownip: string;
	ownip6: string;
	protocol: string;
	rib_routes: number;
	in_rib: boolean;
}

export interface OspfNeighborView {
	node: string;
	interface: string | null;
	cost: number | null;
	iface_type: string;
}

export interface OspfProtocolView {
	protocol: string;
	rib_routes: number;
	in_rib: boolean;
}

export interface InternalTopologyView {
	node_id: string;
	configured: boolean;
	full_mesh_ibgp: boolean;
	ospf_v2: boolean;
	ospf_v3: boolean;
	routers: string[];
	ibgp_peers: IbgpPeerView[];
	ospf_neighbors: OspfNeighborView[];
	ospf: OspfProtocolView[];
	// rib_routes liveness is only as fresh as the last routing snapshot.
	routing_observed: boolean;
	captured_at: string | null;
}

export interface NotifyResponse {
	node_id: string;
	event: string;
	generation: number | null;
	subscribers: number;
	delivered: number;
}

export interface ProvisionOut {
	node_id: string;
	generation: number;
	subscribers: number;
	delivered: number;
}
