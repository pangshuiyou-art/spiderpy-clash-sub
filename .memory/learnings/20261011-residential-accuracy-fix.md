---
name: 住宅判定准确率根因与修复
description: 住宅组混入机房的根因是「有源侧 ASN 被跳过 ip-api 查询 + unknown+ASN 放宽」，已三层修复并验证；附社区判定方案调研结论
type: learning
created: 2026-10-11
updated: 2026-10-11
---

# 住宅判定准确率：根因与修复（2026-10-11）

## 结论速览

- 用户反馈住宅判定不准 → 实测复核（59 个住宅样本过 ip-api）：**19% 错误率（8 个纯机房）**，全部经「unknown+有ASN」放宽通道进入。
- 根因链：源侧元数据传播 ASN → [pipeline] `need_query` 跳过有 ASN 的记录 → `hosting` 证据缺失 → 分类落 unknown → 放宽规则把"unknown+任意 ASN"当住宅。机房 ASN 拦截闸对该批 IP 天然失效（ASN 从未被 ip-api 查过，进不了拦截集合）。
- 三层修复（提交 207d99c，GitHub 运行 38082495376 验证通过）：① `need_query` 全量补查 hosting；② 配置关闭 `unknown_with_asn_as_residential`；③ 待审清单 30 天未活动清理（积压 717 条）。
- 修复验证：4 个纯机房 IP 全部改判 `daily/datacenter/高置信`；住宅组 39→56 个反而更纯（全量补查同时召回了 hosting=False 的真住宅）。
- 「移动 IP 立场」是遗留决策项：中国移动 CGNAT（proxy=True）经 mobile 放宽/白名单仍在住宅组，属 `mobile_as_residential: true` 的既定政策，注册场景是否接受移动 IP 待用户定。

## 判定方法与社区方案调研

现行：源方标注 → CIDR 段库 → **ip-api 免费 batch 接口**（hosting/mobile/proxy，15 批/分钟）→ ASN 白名单（10 个，自学习仅高置信写入）。

社区主流三路线（官方文档已核验）：
- **IP2Proxy LITE PX11**：免费库、文件下载零查询成本，`proxy_type` 含 DCH(数据中心)/RES(住宅代理)；**下载需免费注册账号，无匿名直链**（实测 3 个候选 URL 全 404）。
- **proxycheck.io**：免费 1,000 次/天（注册），`network.type` 直接返回 Residential/Business/Wireless/Hosting，适合做低置信子集的第二意见。
- ipinfo 免费库不含类型字段（类型属付费产品，未核验）。

**第 4 层（加库+第二意见）需用户注册免费账号提供凭证后接入**；本轮三层已把实测误判全拦住，第 4 层是纵深防御。

## 注意事项

- 准确率复核用 ip-api 自身做基准存在循环性；引入第二意见源后应做双源交叉统计。
- `hosting=False` 通道实测 14/14 准确，是放宽关闭后的住宅召回主力，别动它。
- 若住宅组规模骤降，优先查 ip-api 批查询失败率（失败=unknown=归日常）。

## 关联

- [[节点保留机制落地记录]]
- [[免费代理住宅/日常分组订阅流水线]]
- [[CNB代码同步与CDN验证的坑]]
