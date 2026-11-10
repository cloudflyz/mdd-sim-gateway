# Asterisk 补丁总览（selfuse 分支）

本文件汇总 `engine/patches/asterisk/` 下的全部 C 层补丁，以及和它们配套的运行时层改动。
目的是让"我们到底改了 Asterisk 什么"有一份单一、可核对的清单。

## 两类改动，生效方式不同

| 类别 | 位置 | 生效方式 | 需要重编 |
| --- | --- | --- | --- |
| C 补丁 | `engine/patches/asterisk/*.py` | 构建 base 镜像时由 `Dockerfile` 第 106 行逐个执行，直接改写 Asterisk 源码后编译 | **是** |
| 运行时文件 | `engine/render.py`、`engine/swu_ike.py`、`engine/entrypoint.sh`、`engine/templates/*.j2` | `Dockerfile.overlay` 直接 COPY 进镜像 | 否 |

**关键陷阱**：`Dockerfile.overlay` 会用当前仓库算出 `BASE_FP` 写入镜像标签，导致"标签显示含补丁、实际二进制仍是旧编译"。
一旦发生，`install.sh` 会判定"镜像与代码一致"，以后永不重建。判断有没有真正编进去，看两项：

```bash
# 1) 构建日志里必须有 applying 行
grep "applying" /tmp/engine-build.log

# 2) 镜像内产物时间戳必须晚于补丁文件修改时间
docker run --rm --entrypoint sh mdd-sim-gateway/engine:latest \
  -c 'stat -c "%y %n" /usr/lib/asterisk/modules/res_pjsip_outbound_registration.so'
```

## 补丁清单（10 个）

### 注册与 VoLTE

| 补丁 | 目标文件 | 作用 |
| --- | --- | --- |
| `reauth_missing_security_server.py` | `res/res_pjsip_outbound_registration.c` | 收到**不带 `Security-Server` 的 401** 时跳过 sec-agree 协商，直接进入 `VOLTE_STATE_RESPONSE` 应答。3HK 初始 401 与 T-Mobile US 中途 re-REGISTER 都属于这种非标准情况 |
| `volte_registration_expiry_from_contact.py` | 同上 | 注册有效期改从 **200 OK 里我们自己 Contact 头**取。隧道重建后 UE 内网地址变化，原逻辑会取错 |
| `volte_sim_timeout_retry.py` | 同上 | 给 SIM 足够时间应答 AKA 挑战，超时后重试一次，避免首次注册因卡慢直接失败 |
| `ims_public_identity_log.py` | 同上 | 把每次成功 REGISTER 的 `P-Associated-URI` 写入日志，供控制面识别本机号码 |

### DTMF / USSD

| 补丁 | 目标文件 | 作用 |
| --- | --- | --- |
| `dtmf_sample_rate_fallback.py` | `res/res_rtp_asterisk.c` | 发送 DTMF 时使用**对端协商的 telephone-event payload 类型**。部分 IMS（3HK / 华为）协商出的 payload 与 Asterisk 默认值不同，用默认值会导致"按键无效" |
| `ussd_body_capture.py` | `res/res_pjsip_session` 的 `handle_incoming_request()` | 把 3GPP USSD（TS 24.390）请求体暴露出来。运营商把 `#225#` 之类的返回放在对话内请求（甚至 BYE）的 body 里，原生代码直接丢弃 |

### 短信

| 补丁 | 目标文件 | 作用 |
| --- | --- | --- |
| `mt_concat_udh.py` | `res/res_pjsip_messaging` 的 `parse_tpdu()` | 暴露长短信的分段三元组（ref / total / seq）。原生代码解出 UDH 后又丢弃，导致一条长短信被拆成 N 条乱序短消息 |
| `mt_rpack_routing.py` | `chan_pjsip` / `res_pjsip_messaging` 的 `send_rpack()` | 修正 MT 短信的 RP-ACK 路由：必须回给**投递该短信的 SMSC 信令地址**（P-Asserted-Identity），而不是 `smsc_uri`；同时避免 raw-IP request-URI 触发 `EADDRINUSE (120098)` |
| `mo_submit_report.py` | `res/res_pjsip_messaging.c` | 把 SMSC 对我们**主动发出**短信的回执当作提交结果处理，而不是误记成一条收到的短信 |

### 构建期清理

| 补丁 | 目标文件 | 作用 |
| --- | --- | --- |
| `resolve_config_docs.py` | PJSIP 配置文档注册接口 | 把 sysmocom 私有的 `resolve` 字段改用 nodoc API 注册，消除启动时那条假的 `ERROR`（这些字段本来就没有 XML 文档） |

## 运行时层改动（不需重编，overlay 生效）

| 文件 | 改动要点 |
| --- | --- |
| `engine/swu_ike.py` | P-CSCF 变化时重新渲染 `pjsip.conf` 并让 Asterisk **冷重启**（`core restart now`）。注释记录了原因：`module reload` 会在随后的 401 中把已释放的凭据交给 `pjsip_auth_clt_set_credentials()`，触发 memcpy 崩溃 |
| `engine/render.py` | 新增 `dtmf_mode` 配置项（按线路可选，空则沿用 Asterisk 默认 `rfc4733`） |
| `engine/templates/pjsip.conf.j2` | 输出 `dtmf_mode={{ dtmf_mode }}`；注册相关端点参数 |
| `engine/templates/extensions.conf.j2` | DTMF 走 INFO 的处理、拨号计划 |
| `engine/entrypoint.sh`、`notify.py`、`log_capture.py` | 启动流程、通知、日志抓取 |

## 版本与验证记录

- 分支：`selfuse`，基于上游 v1.14.0
- 2026-11-10 全量重建验证：
  - 构建日志出现 10 行 `applying /home/asterisk-build/patches/asterisk/*.py`，无报错
  - 新镜像 `res_pjsip_outbound_registration.so` 时间戳为构建当日，sha256 前缀 `3d7db0a7`
  - 旧镜像同文件 sha256 前缀 `006cc20a`，产物时间为 2026-09-29（早于补丁文件）

## 已知待办

1. `Dockerfile.overlay` 不应把当前仓库的 `BASE_FP` 写进镜像标签，否则指纹会被"洗白"，后续永不重建。
2. 补丁全量编入后，3HK 的 IMS 注册仍返回 403（认证之后），该现象与补丁无关，需在运营商侧确认线路状态。
3. 首次 REGISTER 会携带重启前遗留的 `Authorization` 头，IMS 回 `400 Get Authorization Header Failed or Header Invalid`；属缓存凭据问题，与补丁无关，可后续在 entrypoint 里清理凭据缓存规避。
