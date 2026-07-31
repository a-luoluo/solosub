# SoloSub

SoloSub 是一个用于提交 Soloco 诊断反馈的 Codex Skill。

把 Soloco 诊断包交给 `$solosub` 后，它会：

1. 在本地读取诊断材料；
2. 用通俗易懂的语言说明主要问题；
3. 生成同时适合人和 Agent 阅读的飞书反馈草稿；
4. 展示最终草稿并请求一次确认；
5. 确认后通过 `lark-cli` 提交到飞书多维表格，并回查提交结果。

## 安装

将仓库中的 `solosub` 目录复制到 Codex 全局 Skills 目录：

```text
~/.codex/skills/solosub
```

重启 Codex，然后使用：

```text
$solosub
```

## 使用前配置

每位使用者需要：

- Python 3.10 或更高版本；
- Node.js/npm 和 `@larksuite/cli`；
- 自己的飞书自建应用；
- 为应用和用户授权 `base:form:read`、`base:form:update`、`base:record:read`；
- 在 `solosub/references/form-contract.md` 中填写自己的飞书收集页、share token、Base token 和 table ID。

登录凭据保存在使用者自己的本机环境中，不应提交到仓库，也不要发送给 Agent。

## 数据边界

- 原始诊断包默认只在本机分析，不上传到飞书。
- 默认只提交整理后的文字反馈。
- token、Cookie、密码和密钥会从分析摘录中遮盖。
- 正式提交前必须由用户确认一次最终草稿。

```yaml
skill:
  name: solosub
  input: soloco_diagnostic_package
  output: human_and_agent_readable_feishu_feedback
  confirmation_required: true
  raw_bundle_uploaded_by_default: false
```
