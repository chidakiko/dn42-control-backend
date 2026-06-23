# Peering with AS4242420028

We are an [DN42](https://dn42.dev) network operating a small Asia‑Pacific +
US backbone. We are happy to peer with anyone in DN42.

| | |
|---|---|
| **ASN** | `AS4242420028` |
| **Region** | East Asia (HK / TW / JP) + US West |
| **IGP** | OSPF + iBGP full‑mesh |
| **Tunnel** | WireGuard only |
| **Routing** | Multiprotocol eBGP (IPv4 + IPv6) over link‑local |
| **Contact** | <it@ngworks.cn> |

---

## Points of presence

Pick the node closest to you and open a tunnel against the listed
**endpoint** and **public key**. Endpoints are domain names — please resolve
them at connect time so they keep working if we renumber. Every endpoint is
**dual‑stack** (A + AAAA); connect over IPv4 or IPv6.

### International PoPs

| PoP | Location | Endpoint | WireGuard public key |
|-----|----------|----------|----------------------|
| **hkg1** | Hong Kong | `hkg1.edge.ngworks.org:22943` | `Q9kncPFCcdOKxfcP6ai2rg3QlwVVnp4W5TahwlY3RBc=` |
| **tpe1** | Taipei, TW | `tpe1.edge.ngworks.org:41027` | `xrI6PtNid+TDc/EWgYBZVfRRKI0qawfOyZ4LLYmqNTY=` |
| **tyo1** | Tokyo, JP | `tyo1.edge.ngworks.org:53618` | `nh7YJr4zF23R2HCBsCDC9KNNTE16nHICr4XwhMf63jo=` |
| **lax1** | Los Angeles, US | `lax1.edge.ngworks.org:49185` | `1i1Tv7fM+wIfX8w5b50KiBdUhcNy1CxAfzr+Jfft93c=` |

### Mainland China PoPs

For peers **physically inside mainland China**. **Do not tunnel to these from
outside China** — a raw WireGuard tunnel across the GFW risks the host being
blocked; from abroad, use an International PoP instead.

| PoP | Location | Endpoint | WireGuard public key |
|-----|----------|----------|----------------------|
| **pvg2** | Shanghai | `pvg02.peer.dn42.ngworks.cn:26508` | `tMxc1k51TwiyFZ2TrWz5dN6qZjqohKkUOmg3t3AlgxE=` |
| **can2** | Guangzhou | `can02.peer.dn42.ngworks.cn:44731` | `/KmWsE8F6ncZRf+t+9xVLJ8S6DyvZSuyhTffV8f8+Fs=` |

> **Ports are per‑peer.** The ports above are our advertised defaults; the
> final WireGuard port is confirmed when we set the session up, so there is no
> single shared listener. Tell us which PoP you want and we will hand you the
> exact `endpoint:port` for your tunnel.

---

## Requirements

- A registered DN42 ASN and at least one DN42 prefix in the registry.
- WireGuard (we do not run other tunnel types).
- Multiprotocol BGP with extended next‑hop (IPv4 carried over the IPv6
  session is fine).
- Keep the session up; flapping sessions may be torn down.

## WireGuard

```ini
[Interface]
PrivateKey = <your private key>
ListenPort = <your port — any free UDP port>
# Link-local address on the tunnel, e.g. fe80::<your-asn-tail>/64
Address   = fe80::xxxx/64
MTU       = 1420            # drop to 1280 if you see PMTU blackholes

[Peer]
PublicKey  = <our key from the table above>
Endpoint   = <our endpoint from the table above>
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
```

Send us your **public key**, your **WireGuard endpoint** (`host:port`, or
"none / I dial you" if you are behind NAT) and your **link‑local address**.

## BGP

We run **multiprotocol eBGP over IPv6 link‑local**, announcing both IPv4 and
IPv6 DN42 routes. Example (BIRD 2):

```
protocol bgp ng_AS4242420028 {
    local fe80::xxxx%dn42-ng as <your-asn>;
    neighbor fe80::<our-ll>%dn42-ng as 4242420028;
    direct;
    ipv4 { extended next hop on; import filter dn42_import; export filter dn42_export; };
    ipv6 { extended next hop on; import filter dn42_import; export filter dn42_export; };
}
```

- We tag routes with the standard DN42 latency/region large communities.
- We honour `(64511, 1..9)` latency and standard blackhole communities.

## How to request peering

Open a request via the contact above with:

1. Your **ASN** and DN42 **prefixes**.
2. The **PoP** you want (e.g. `tyo1`, `lax1`).
3. Your WireGuard **public key**, **endpoint** and **link‑local** address.

We will reply with the exact `endpoint:port`, our link‑local address and the
session will be live shortly after.
