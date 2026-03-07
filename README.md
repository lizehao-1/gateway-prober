# Gateway Prober

一个用于检测 OpenAI-compatible 网关能力的小工具。

你只需要输入：

- 网关根地址
- API Key

它就会自动检测这个网关是否支持：

- 模型列表
- `chat/completions`
- 工具调用 `tool_calling`
- `responses`
- `embeddings`
- 图片生成
- 文档相关端点

这个项目适合以下场景：

- 接第三方 LLM 网关前先做兼容性检查
- 判断一个 Key 适不适合做多智能体系统
- 判断一个网关能不能做 RAG、图片生成、自动化工作流
- 给团队或客户做快速技术验收

## 功能形式

项目提供两种使用方式：

1. 本地 Web 页面
2. 命令行 CLI

此外，还提供了一套 Cloudflare Pages 版本，方便直接部署到公网域名。

## 本地运行

先安装依赖：

```powershell
pip install -r requirements.txt
```

启动本地 Web：

```powershell
python .\src\web_app.py
```

打开：

[http://127.0.0.1:5050](http://127.0.0.1:5050)

Windows 下也可以直接用：

```powershell
.\start.bat
```

或：

```powershell
.\start.ps1
```

## CLI 用法

文本报告：

```powershell
python .\src\probe_gateway.py --base-url "https://example.com" --api-key "sk-xxx"
```

JSON 输出：

```powershell
python .\src\probe_gateway.py --base-url "https://example.com" --api-key "sk-xxx" --format json
```

## Cloudflare Pages 版本

项目内置 Cloudflare Pages 前端和函数：

- 页面入口：`cf-pages/public/index.html`
- 前端脚本：`cf-pages/public/app.js`
- 页面样式：`cf-pages/public/styles.css`
- 服务端探测接口：`cf-pages/functions/api/probe.js`

部署示例：

```powershell
wrangler pages project create gateway-prober --production-branch main
wrangler pages deploy .\cf-pages\public --project-name gateway-prober --branch main
```

如果要绑定自定义域名，除了在 Pages 项目里绑定域名之外，还需要在 Cloudflare DNS 里把记录指向对应的 `pages.dev` 域名。

## 每项能力代表什么

- `models`
  用来确认这个网关暴露了哪些模型名。
- `chat_completions`
  最基础的文本能力。聊天、总结、代码辅助基本都依赖它。
- `tool_calling`
  适合多智能体编排、函数调用、外部工具接入。
- `responses`
  适合接较新的统一接口和更完整的 agent workflow。
- `embeddings`
  适合 RAG、向量检索、知识库搜索。
- `images`
  适合图片生成、海报、封面、视觉素材。
- `docs`
  主要是辅助判断文档端点和实现风格，不是核心能力。

## 怎么理解检测结果

- 如果 `chat_completions` 通过：
  这个 Key 至少适合普通文本任务。
- 如果 `tool_calling` 也通过：
  说明它更适合做多智能体系统或自动化工作流。
- 如果 `responses` 也通过：
  更适合接新式 SDK，后续扩展空间更大。
- 如果 `embeddings` 不通过：
  不建议直接拿这个网关做 RAG。
- 如果 `images` 不通过：
  更适合文本任务，不适合图像生成。

## 注意事项

- 页面不会持久化保存你的 API Key。
- 不同第三方网关兼容程度差异很大。
- “能列模型”不等于“能稳定调用所有接口”。
- 很多网关能跑文本，但不一定支持 `embeddings` 或 `images`。

## 仓库

[https://github.com/lizehao-1/gateway-prober](https://github.com/lizehao-1/gateway-prober)
