# dn42_common

`dn42_common` 是跨包共享的纯工具层。它不包含业务模型，不包含模板渲染逻辑，也不调用 Docker。上层包使用它提供的校验器、命名规则、label、community 和 Jinja 工具。

## 文件结构

| 文件 | 内容 |
| --- | --- |
| `dn42_common/communities.py` | DN42 community 编码 |
| `dn42_common/crypto.py` | WireGuard keypair / RSA-OAEP 恢复密钥托管（惰性依赖 `cryptography`） |
| `dn42_common/io.py` | `atomic_write_json` / `atomic_write_text`（tmp + 原子替换） |
| `dn42_common/jinja.py` | Jinja2 environment、`shell_quote`、`yaml_quote` |
| `dn42_common/labels.py` | Docker 资源 label 常量和 helper |
| `dn42_common/naming.py` | project、container、identifier、`agent_id_for` 命名规则 |
| `dn42_common/serialization.py` | `canonical_json_dumps` / `canonical_sha256_hex`（跨进程一致的内容寻址哈希） |
| `dn42_common/validators/agent_token.py` | Agent token 形状校验 |
| `dn42_common/validators/asn.py` | ASN 校验 |
| `dn42_common/validators/dn42_space.py` | DN42 地址空间校验 |
| `dn42_common/validators/domain.py` | domain / hostname 校验 |
| `dn42_common/validators/ip.py` | IP address/network/interface 校验 |
| `dn42_common/validators/timestamp.py` | ISO-8601 timestamp 校验 |
| `dn42_common/validators/wireguard.py` | WireGuard key / endpoint 校验 |

## IP 校验

| 函数 | 作用 |
| --- | --- |
| `validate_ip_address(value, version=None)` | 校验 IP 地址，可强制 IPv4 或 IPv6 |
| `validate_ip_network(value, strict=False)` | 校验 CIDR network |
| `validate_ip_interface(value)` | 校验 `addr/prefix` 接口地址 |
| `split_ipv6_zone(value)` | 把 `fe80::1%eth0` 拆成地址和 zone |
| `validate_ip_address_with_optional_zone(value)` | 允许 IPv6 link-local 带 `%zone` |
| `is_address_in_prefix(addr, prefix)` | 判断地址是否属于 prefix |
| `is_ipv6_link_local(value)` | 判断是否是 `fe80::/10` |

## DN42 地址空间

| 常量 | 值 |
| --- | --- |
| `DN42_IPV4_SPACE` | `172.20.0.0/14` |
| `DN42_IPV6_SPACE` | `fd00::/8` |
| `DN42_ANYCAST_IPV6_SPACE` | `fd42:d42:d42::/48` |

常用函数：

```python
is_dn42_address(value)
is_dn42_network(value)
validate_dn42_ipv4_network(value)
validate_dn42_ipv6_network(value)
```

`validate_dn42_ipv4_network` 默认允许 anycast 和 transfer network，拒绝 closed 和 reserved network。

## ASN 校验

| 名称 | 说明 |
| --- | --- |
| `validate_asn(value)` | 校验 ASN 范围 `[1, 4294967295]` |
| `is_private_asn(value)` | 判断是否属于 RFC 6996 或 RFC 7300 私有 ASN 范围 |

## WireGuard 校验

| 函数 | 作用 |
| --- | --- |
| `validate_wireguard_key(value)` | 校验 44 字符 base64 WireGuard key，解码后必须是 32 字节 |
| `is_wireguard_key(value)` | 非抛出版本 |
| `validate_wireguard_endpoint(value)` | 校验 `host:port` 或 `[ipv6]:port` |

错误信息不会包含密钥原文。

## Domain 校验

| 函数 | 作用 |
| --- | --- |
| `validate_domain_name(...)` | 校验 domain，可允许末尾点、下划线、单 label |
| `validate_hostname(value)` | 校验 hostname |
| `is_domain_name(value, **kwargs)` | 非抛出版本 |
| `is_dn42_zone(value)` | 判断是否是 `.dn42`、`.neo` 或反向 DNS zone |

DN42 forward zone 常见为单 label，例如 `dn42` 或 `neo`，因此 DNS schema 会使用 `require_multi_label=False`。

## Labels

受管 Docker 资源使用这些 label：

| 常量 | 值 |
| --- | --- |
| `LABEL_MANAGED` | `dn42.managed` |
| `LABEL_MANAGED_VALUE` | `true` |
| `LABEL_NODE_ID` | `dn42.node_id` |
| `LABEL_CONFIG_HASH` | `dn42.config_hash` |
| `LABEL_COMPONENT` | `dn42.component` |

函数：

```python
network_labels()
container_labels(node_id, component, config_hash)
node_id_filter(node_id)
```

`dn42.config_hash` 只是身份的**存储位置**；哈希的计算在 agent 决策层
（`agent.planner.definition`），输入是即将发给 Engine API 的容器定义
payload，与 schema 形状解耦。

## Naming

| 函数 | 作用 |
| --- | --- |
| `normalize_identifier(value)` | 把字符串归一化为小写、`a-z0-9_-` 和 `-` |
| `node_project_name(node_id, override=None)` | 生成节点 runtime 项目名（容器/网络名前缀） |
| `service_container_name(project, service)` | 生成 `<project>-<service>-1` 容器名 |

## Jinja

`create_environment(template_dir)` 创建启用 `StrictUndefined` 的 Jinja 环境，并注册：

| filter | 作用 |
| --- | --- |
| `shell_quote` | POSIX shell 单引号转义 |
| `yaml_quote` | YAML 双引号转义 |

`StrictUndefined` 会让未定义变量立即抛错，避免模板把缺失字段静默渲染为空字符串。

## 设计边界

| 可以放入 | 不应放入 |
| --- | --- |
| 被多个包复用的无状态工具 | Pydantic 业务模型 |
| 常量和枚举辅助 | Jinja 模板内容 |
| 校验器 | Docker 调用 |
| 命名和 label 规则 | 文件写盘逻辑 |
