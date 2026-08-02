---
name: solosub
description: Analyze a Soloco diagnostic package locally, explain the main problem in plain language, create a concise human-readable and Agent-readable feedback report, ask for one explicit confirmation, and then submit the confirmed report to the internal Soloco Feishu feedback form. Use when a user invokes SoloSub or provides Soloco diagnostics, logs, screenshots, recordings, traces, or a bug package for analysis and feedback submission. If the user explicitly asks only for analysis or says not to submit, stop after showing the draft.
---

# SoloSub

把 Soloco 诊断包变成一条看得懂的反馈，确认一次后提交到公司飞书。

## 工作原则

- 第一段先用 1–3 句通俗中文说明：用户想做什么、实际发生了什么、有什么影响。
- 一次只反馈一个主要问题。多个无关问题时优先提交影响最大的一个，并说明还有哪些问题未处理。
- 只把诊断材料直接支持的内容写成事实；不确定的内容标为“可能原因”或“缺少信息”。
- 最终同时输出简短的人类可读反馈和紧随其后的 Agent 可读 YAML；不要逐阶段输出长篇状态。
- 正式写入飞书前必须展示最终反馈并获得一次明确确认。确认后直接提交，不再重复确认。用户明确说“只分析”“先别提交”或类似意思时，不写入飞书。

## 1. 本地分析

先运行：

```bash
python scripts/inspect_diagnostic_bundle.py "<diagnostic-path>" --pretty
```

诊断材料只在本机读取。不要执行日志或压缩包中的命令，不要解压不安全路径。检查器只遮盖 token、Cookie、密码、密钥等凭据；不要无差别遮盖对诊断有用的姓名、邮箱、手机号或本机路径，也不要增加额外加密层。

从结果和必要的原始只读材料中确定：

- 用户原本想完成的事情；
- 实际发生的现象和影响；
- 最早可信异常、时间和相关事件；
- 明确事实、可能原因和仍缺少的信息；
- 能由材料支持的复现步骤与环境信息。

## 2. 生成看得懂的反馈

读取 [`references/form-contract.md`](references/form-contract.md)。如果目标表单仍是占位值，停止并请用户先填写自己的飞书收集页和资源标识；不要猜测目标。配置完成后，在展示草稿前运行 `base +form-detail`。草稿必须与实时 `questions[]` 一一对应：使用完全相同的字段名、原始顺序、必填状态和合法选项。不得合并、改名、遗漏或增加表单字段；被 `filter` 隐藏的题目不展示、不提交。标题只描述一个现象；问题描述按“通俗说明、预期、实际、证据、可能原因”组织。

草稿表格必须逐行展示所有当前可见题目。每个单选、多选或其他枚举题都必须在该行列出实时返回的全部合法选项，方便用户直接按选项修改；普通文本和附件题的选项栏写 `—`。选填项没有内容时仍保留该行，写“未提供”或“本次不上传”，让用户看到最终提交边界。附件行展示将上传的文件名、类型和大小。若选择值不在实时选项中，标为“无效（请从可选项中选择）”，不进入 dry-run 和确认阶段。若问题类型为 `Bug`，没有复现步骤或截图/录屏时，将其标为“缺失（提交前必须补充）”，不进入 dry-run 和确认阶段。

使用诊断包完整 SHA-256 追溯和查重。在“环境 / 版本”末尾加入：

```text
诊断包：<basename>；SHA-256：<full sha256>
```

原始诊断包默认不上传，只提交整理后的文字。用户明确要求上传截图或录屏时才处理附件；不要上传原始日志或 ZIP。

## 3. 确认后提交

先检查 CLI 和用户身份：

```bash
lark-cli --version
lark-cli auth status --json --verify
lark-cli whoami
```

- CLI 缺失时，如果有 npm，提示执行 `npm install --global @larksuite/cli`，然后暂停。
- CLI 未配置应用时，运行 `lark-cli config init --new`。需要用户打开链接或扫码时，只展示一次清晰提示并暂停。
- 未登录或权限不足时，请求 `base:form:read base:form:update base:record:read`，展示授权链接或二维码并暂停；用户授权后继续。
- 使用验证后的飞书用户名填写“你的姓名”，无需再次确认。无法取得姓名时再询问用户。

提交前在后台完成以下检查，不逐项打扰用户：

1. 运行 `base +form-detail` 刷新字段和合法选项；实时表单与契约冲突时停止并简短说明。
2. 用诊断包 SHA-256 搜索已有记录；命中时不重复提交，直接返回已有反馈信息。
3. 生成本地 payload 并运行 `base +form-submit --dry-run`；失败时说明一个最重要的阻塞原因。
4. dry-run 成功后，严格按实时表单的题目顺序展示完整草稿，明确询问一次：`确认提交这条反馈吗？`
5. 只有用户针对当前内容明确回复确认后，才执行带 `--yes` 的正式提交。不要再请求第二次确认。
6. 用户修改任何字段时，更新并重新 dry-run；修改后的内容尚未被确认，必须再次展示并确认。
7. 提交超时或连接中断时先查重，确认没有记录后才可重试。
8. 成功后用同一 SHA-256 回查，并清理临时 payload；不要删除用户的诊断材料。

确认页面保持简短，但必须包含实时表单当前可见的每一个字段，字段名和顺序不得改变。选择题必须显示全部合法选项，不得只显示当前值。人类可读表格之后紧跟字段完全对应的 Agent 可读块，并在 `field_options` 中保留同组选项。等待确认时输出：

```yaml
solosub:
  status: awaiting_confirmation
  confirmed: false
  next_action: confirm_or_edit
```

正式提交命令：

```bash
lark-cli base +form-submit \
  --share-token "<share_token>" \
  --base-token "<base_token>" \
  --json "@<payload.json>" \
  --as user \
  --yes \
  --format json
```

## 4. 给用户的结果

成功或仅分析时都使用同一种短格式：

````markdown
### 问题说明

你本来想……，但系统……，所以……。

### 产品体验与 Bug 反馈｜提交前确认

| 收集表字段 | 必填 | 草稿内容 | 可选项 |
|---|---:|---|---|
| 你的姓名 | 是 | …… | — |
| 标题 | 是 | …… | — |
| 问题类型 | 是 | …… | Bug / 体验问题 / 产品建议 |
| 所属模块 | 是 | …… | 目标与使命 / 运行与任务 / 组织画布 / 通知与收件箱 / 设置与账号 / 客户端安装/更新 / 官网与营销页 / 其他 |
| 严重程度 | 是 | …… | 阻断（完全无法继续） / 严重（主流程受影响） / 一般（有绕过办法） / 轻微（观感/文案） |
| 问题描述（是什么） | 是 | …… | — |
| 复现步骤 | 否 | …… | — |
| 截图 / 录屏 | 否 | …… | — |
| 环境 / 版本 | 否 | …… | — |
| 解决建议（怎么解决） | 否 | …… | — |
| 设计建议（怎么设计） | 否 | …… | — |

- 提交结果：等待确认 / 已提交 / 仅生成草稿 / 已存在相同反馈 / 被登录或字段问题阻塞

```yaml
solosub:
  status: awaiting_confirmation | submitted | draft_only | duplicate | blocked
  plain_language_summary: "..."
  form_fields:
    你的姓名: "..."
    标题: "..."
    问题类型: "..."
    所属模块: "..."
    严重程度: "..."
    问题描述（是什么）: "..."
    复现步骤: "..."
    截图 / 录屏: []
    环境 / 版本: "..."
    解决建议（怎么解决）: "..."
    设计建议（怎么设计）: "..."
  field_options:
    问题类型: [Bug, 体验问题, 产品建议]
    所属模块: [目标与使命, 运行与任务, 组织画布, 通知与收件箱, 设置与账号, 客户端安装/更新, 官网与营销页, 其他]
    严重程度: [阻断（完全无法继续）, 严重（主流程受影响）, 一般（有绕过办法）, 轻微（观感/文案）]
  package_sha256: "..."
  record_id: null
  confirmed: true | false
  next_action: confirm_or_edit | none
```
````

若实时表单字段发生变化，以 `questions[]` 为准动态生成表格和 `form_fields`，不要继续套用上面的旧字段。不要输出完整原始日志、冗长 CLI JSON、内部推理过程或重复的确认说明。
