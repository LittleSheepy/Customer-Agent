# 电商智能客服系统

电商AI客服桌面应用程序，基于 PyQt6 构建，支持多平台渠道集成，集成 AI 大模型实现智能自动回复。

> 本项目基于 [JC0v0/Customer-Agent](https://github.com/JC0v0/Customer-Agent) 进行深度优化，主要围绕 **Agent 架构升级**、**RAG 知识库增强**、**UI 聊天信息展示** 等方面进行了全面重构与功能扩展。

## 相比原版的优化与新功能

### 1. Agent 架构全面重构

- **多 Agent 协作架构（MultiAgent）**：引入 Supervisor + SubAgent 分层调度模式，将用户意图自动路由到专属子 Agent（商品咨询 / 售后服务 / 闲聊），每个子 Agent 拥有独立的系统提示词和工具集，提升回复专业度和上下文准确性
- **Agentic RAG Agent**：全新自反思 RAG 闭环 Agent，支持"检索 → 相关性评估 → 生成 → 质量评估 → 查询优化 → 迭代检索"的多轮自反思循环，显著提高知识库检索回答的准确率
- **Agent 通用基类（AgentBase）**：采用模板方法模式抽取公共骨架（初始化锁、会话串行、历史加载/压缩/持久化、ReAct Agent Loop），子类只实现业务差异钩子，消除大量重复代码
- **ReAct Agent Loop**：Agent 内部采用 ReAct 循环（LLM → tool_calls → 执行工具 → 回传 → 循环），支持多轮工具调用，替代原版的单次调用模式
- **Agent 工厂模式**：通过配置 `agent.active` 切换 Agent 类型（`agentic` / `multi`），延迟导入避免循环依赖

### 2. 意图分类与路由优化

- **三层分类降本策略**：规则快速通道（关键词/白名单） → 会话级意图缓存（LRU 有界 + TTL 过期 + 置信度门控） → LLM 分类，有效降低 LLM 调用次数
- **意图漂移检测**：当用户话题切换时自动识别并熔断缓存，防止历史意图误导后续回复
- **结构化输出**：使用 LangChain `with_structured_output` 保证分类结果格式可靠

### 3. RAG 知识库增强

- **完整 RAG 模块化架构**：
  - **Chunker**：支持基础分块和语义分块（Semantic Chunking）
  - **Retriever**：BM25 检索 + 向量检索 + 混合检索（Hybrid Retriever），支持多路召回融合
  - **Reranker**：LLM 驱动的精排重排序，过滤低相关性文档
  - **VectorStore**：ChromaDB 向量存储集成
- **Self-RAG 自反思管线**：按店铺隔离的 LRU 缓存 Pipeline，支持检索质量自评、答案质量评估和查询优化迭代
- **新增工具**：`self_rag_search` 让 Agent 可主动调用 RAG 精检索

### 4. 韧性与可观测性

- **弹性模型（ResilientModel）**：内置断路器（CircuitBreaker）+ 模型降级回退机制，API 异常时自动切换备用模型，避免服务中断
- **中间件管道（MiddlewarePipeline）**：输入校验 → 日志 → 指标采集 → 超时控制，统一处理横切关注点
- **可观测性模块（observability）**：请求级 trace scope、指标计数器，方便后续接入 APM

### 5. UI 优化

- **聊天信息展示**：UI 新增聊天记录展示功能，可在界面中实时查看买家与 AI 客服的对话内容
- **知识库管理界面**：独立的 `Knowledge_ui` 模块，支持知识库文档的可视化管理
- **自动回复界面**：独立的 `auto_reply` 子模块和 `auto_reply_ui.py`，提供更直观的配置体验

### 6. 渠道与 API 扩展

- **新增 API 接口**：`get_custom_service_info`（客服信息）、`get_messages`（消息获取）、`get_messages_users`（消息用户列表），扩展了与拼多多平台的交互能力
- **Cookie 缓存增强**：优化了 Cookie 持久化和刷新逻辑，提升连接稳定性
- **消息处理精简**：移除了原版的 `message_rules.py`，将消息规则逻辑整合到 Agent 决策流程中，由 AI 统一管理回复策略

## 下载安装

普通用户无需配置 Python 环境，直接下载安装包即可使用。

下载地址：[https://github.com/LittleSheepy/Customer-Agent/releases/latest](https://github.com/LittleSheepy/Customer-Agent/releases/latest)

在页面的 Assets 中下载 `Agent-Customer-Setup-<版本号>.exe`（约 100 MB），双击运行即可安装。

- 安装到用户目录，无需管理员权限
- 双击安装向导（中文）→ 桌面快捷方式 → 装完即用
- 安装包已内置 Playwright 驱动，添加账号时自动调用本机已安装的 Chrome 或 Edge 浏览器完成登录，无需额外安装 Playwright 或 Chromium

## 功能特性

- **多渠道支持**：目前支持拼多多平台 WebSocket 实时消息接收
- **AI 智能回复**：基于自研 Agent 框架（不依赖 Agno），多轮工具调用 + 会话上下文管理
- **多 Agent 协作**：Supervisor 智能路由，自动将用户意图分发到商品咨询 / 售后服务 / 闲聊专属子 Agent
- **Agentic RAG**：自反思 RAG 闭环，检索-评估-生成-迭代，知识库回答准确率显著提升
- **AI 主动推荐**：客服代理可主动获取商品列表、发送商品卡片给用户
- **双知识库体系**：产品知识库 + 客服知识库，分别检索商品信息与售后/物流/退款等政策
- **商品知识自动同步**：从拼多多 API 拉取商品列表，调用多模态 LLM 提取产品知识入库
- **关键词转人工**：自动识别用户意图，支持关键词触发转人工服务
- **消息队列处理**：异步消息队列 + 处理器链，支持高并发场景
- **自动重连机制**：WebSocket 连接支持断线自动重连和心跳检测
- **弹性降级**：内置断路器 + 模型降级回退，API 异常时自动切换备用模型

### AI Agent 可用工具

| 工具名称 | 功能描述 |
|---------|---------|
| `get_shop_products` | 获取店铺商品列表（支持价格区间、销量、库存、标签等） |
| `send_goods_link` | 向用户发送商品卡片链接 |
| `get_product_knowledge` | 查询指定商品的详细知识（成分、规格、用法、价格等） |
| `search_customer_service_knowledge` | 搜索客服知识库（售后、物流、退款等政策问答） |
| `self_rag_search` | 调用 Self-RAG 精检索管线，获取高质量知识库上下文 |
| `transfer_conversation` | 转接会话给人工客服 |

## 环境要求

- Python >= 3.11
- Windows 操作系统（打包为 exe 后可在 Windows 上独立运行）

## 安装

```bash
# 安装依赖
uv sync
```

## 启动

```bash
python app.py
```

## 配置

首次运行后会生成 `config.json`。推荐在"系统设置 → LLM 模型配置"中填写 API Key；示例、日志和 CI 都不需要环境变量，也不包含真实密钥。

应用通过 LiteLLM 直接调用 Chat Completions，不需要单独启动 LiteLLM Proxy。设置页提供以下六个供应商：

| 设置页供应商 | LiteLLM 路由前缀 | 默认 Base URL | Base URL 规则 |
|------------|-----------------|--------------|-------------|
| DeepSeek | `deepseek/` | `https://api.deepseek.com` | 可留空使用默认值，或填写 HTTPS 覆盖 |
| 火山引擎 | `volcengine/` | `https://ark.cn-beijing.volces.com/api/v3` | 可留空使用默认值，或填写 HTTPS 覆盖 |
| OpenAI-compatible | `openai/` | 无 | 必须填写符合 Chat Completions 约定的 HTTPS Base URL；模型名可任意填写 |
| Kimi/Moonshot | `moonshot/` | `https://api.moonshot.cn/v1` | 可留空使用默认值，或填写 HTTPS 覆盖 |
| 智谱/Z.AI | `zai/` | `https://open.bigmodel.cn/api/paas/v4` | 可留空使用默认值，或填写 HTTPS 覆盖 |
| Qwen/DashScope | `dashscope/` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 可留空使用默认值，或填写 HTTPS 覆盖 |

每个供应商都需要手动填写模型标识和 API Key。OpenAI-compatible 不会套用内置供应商的模型命名规则；例如可以填写组织内部模型名和其 HTTPS 端点。

**端点安全规则**：远程端点必须使用 HTTPS 并保持证书校验；带账号密码的 URL、云实例元数据/链路本地地址和跨主机重定向会被拒绝。本地或私有端点（如本机或局域网内的 Ollama、LM Studio，可使用 HTTP）只有在设置页明确选择"允许本地或私有端点"后才可使用。使用自定义端点或能力未知的模型调用工具时，设置页会显示未验证状态并要求基于当前供应商、模型、端点和工具策略确认；明确不支持工具调用的模型不能启用，应用不会静默关闭工具。

旧版本没有 `llm.provider` 的配置会在加载时迁移：历史 Ark/火山引擎端点迁移为 `volcengine`，其他无法识别的自定义端点迁移为 `openai_compatible`，模型名、端点和 API Key 保持不变。API Key 仍使用 Windows DPAPI 保护后落盘。

保存成功只更新新的 profile。已经运行的账号继续使用自己的 profile 快照，需要按现有账号停止/重新启动流程后才会使用新配置。

产品知识同步会把必要的商品文本和图片发送给所选供应商。供应商侧的数据保留、训练、跨境传输和隐私政策由用户与供应商自行确认；应用不会声称可以控制供应商的留存策略，也不会发送 API Key、Cookie、授权头或内部 profile 字段。

## 开发规范

### 新增/修改接口前

- 先用 curl 或 Python 脚本测试接口，确认真实请求参数、请求头、响应结构
- 根据实际响应结构修改解析代码，不要凭猜测写字段名
- 修改后用 mock 数据或真实调用验证解析逻辑

> 例如：修改 `product_manager.py` 的商品列表接口时，先用 curl 测试接口，确认数据在 `result.onSaleGoods` 字段而非 `result.goodsList`，字段名为驼峰 `goodsId` 而非下划线 `goods_id`，价格单位是"分"需除以 100 转换为"元"

## 构建 Windows 安装包

在 Windows 上运行：

```bash
python scripts/build_win_exe.py --clean
```

该命令会依次完成：

- 用 PyInstaller 打包应用（产物 `dist/AgentCustomer/`，含内置 Playwright 驱动）
- 调用 Inno Setup 压缩为单个安装程序（产物 `dist/installer/Agent-Customer-Setup-<版本号>.exe`）

版本号自动从最近的 git tag 读取（去掉前导 `v`），也可用环境变量 `APP_VERSION` 指定。

若仅需 PyInstaller 的 onedir 目录、不生成安装程序，加 `--skip-installer`。

### CI 自动构建

推送 `v*` 格式的 tag 会触发 GitHub Actions 自动构建并发布 Release：

```bash
git tag v1.4
git push origin v1.4
```

构建产物（`Agent-Customer-Setup-<版本号>.exe`）会自动上传到该 tag 对应的 Release 页，即上文「下载安装」的来源。

## 项目结构

```text
Agent-Customer/
├── Agent/                      # AI 代理模块
│   ├── AgentBase.py            # Agent 通用基类（模板方法 + ReAct Loop）
│   ├── factory.py              # Agent 工厂（按配置创建 Agent 实例）
│   ├── llm_client.py           # LLM 客户端工厂
│   ├── message_builder.py      # 消息构建器（system prompt + 历史 + context）
│   ├── middleware.py            # 中间件管道（校验/日志/指标/超时）
│   ├── resilience.py           # 弹性模型（断路器 + 模型降级回退）
│   ├── session_manager.py      # 会话管理器（历史加载/压缩/持久化）
│   ├── tool_decorator.py       # 工具装饰器与注册表
│   ├── prompts_utils.py        # 提示词工具与模板片段
│   ├── utils.py                # Agent 工具函数
│   ├── AgenticRAGAgent/        # 自反思 RAG Agent
│   │   ├── agentic_rag_agent.py  # Agent 实现（继承 AgentBase）
│   │   ├── prompts.py            # 系统提示词与评估提示词
│   │   ├── router.py             # 智能路由
│   │   └── schemas.py            # 结构化输出模型
│   ├── MultiAgent/             # 多 Agent 协作系统
│   │   ├── supervisor.py       # Supervisor Agent（意图调度 + 会话管理）
│   │   ├── router.py           # 意图分类器（规则/缓存/LLM 三层）
│   │   ├── prompts.py          # 意图分类提示词与关键词库
│   │   ├── schemas.py          # 意图分类结构化输出
│   │   └── SubAgent/           # 子 Agent 集合
│   │       ├── base.py         # 子 Agent 基类（继承 AgentBase）
│   │       ├── product_agent.py  # 商品咨询子 Agent
│   │       ├── after_sales_agent.py  # 售后服务子 Agent
│   │       └── chat_agent.py     # 闲聊子 Agent
│   ├── RAG/                    # RAG 知识库模块
│   │   ├── Chunker/            # 文档分块（基础 + 语义）
│   │   ├── Retriever/          # 检索器（BM25 + 向量 + 混合）
│   │   ├── Reranker/           # LLM 精排重排序
│   │   ├── VectorStore/        # ChromaDB 向量存储
│   │   ├── self_rag.py         # Self-RAG 自反思管线
│   │   ├── embedder.py         # 文本嵌入服务
│   │   ├── document_loader.py  # 文档加载器
│   │   ├── document_service.py # 文档索引服务
│   │   └── vector_store.py     # 向量搜索服务
│   └── tools/                  # Agent 工具集
│       ├── get_product_knowledge.py
│       ├── get_product_list.py
│       ├── search_customer_service_knowledge.py
│       ├── self_rag_search.py    # Self-RAG 精检索工具
│       ├── send_goods_link.py
│       └── move_conversation.py
├── Channel/                    # 渠道集成
│   └── pinduoduo/
│       ├── core/               # 连接、生命周期、状态、消息处理
│       ├── utils/API/          # 拼多多 API 封装
│       ├── cookie_cache.py     # Cookie 缓存
│       └── pdd_login.py        # 登录流程
├── Message/                    # 消息处理（队列 + 处理器链）
│   ├── core/                   # consumer / handlers / queue
│   └── handlers/               # 预处理器、AI、关键词处理器
├── bridge/                     # 桥接模块（Context/Reply/SendService）
├── core/                       # 核心服务（DI 容器、连接状态）
├── database/                   # 数据库（SQLAlchemy + 知识服务 + 商品同步）
├── service/                    # 服务层（账号/关键词服务薄封装）
├── ui/                         # PyQt6 用户界面
│   ├── Knowledge_ui/           # 知识库管理界面
│   ├── auto_reply/             # 自动回复子模块
│   ├── auto_reply_ui.py        # 自动回复配置界面
│   ├── main_ui.py              # 主界面
│   ├── user_ui.py              # 用户/账号管理界面
│   ├── setting_ui.py           # 系统设置界面
│   ├── keyword_ui.py           # 关键词管理界面
│   └── log_ui.py               # 日志查看界面
├── utils/                      # 工具模块（日志、路径、可观测性等）
├── scripts/                    # 构建脚本
└── app.py                      # 应用入口
```

## 技术栈

| 类别 | 技术 |
|-----|------|
| UI 框架 | PyQt6 + pyqt6-fluent-widgets |
| AI 框架 | 自研 Agent 框架（MultiAgent + Agentic RAG + ReAct Loop）+ LiteLLM 直接异步调用 |
| RAG | 自研 RAG 管线（BM25 + 向量混合检索 + LLM Reranker + ChromaDB + Self-RAG） |
| 数据库 | SQLAlchemy + SQLite |
| 中文分词 | jieba（知识库检索） |
| Token 统计 | tiktoken |
| 异步通信 | asyncio + websockets |
| 文档解析 | pypdf + python-docx + openpyxl + xlrd |
| 日志 | Loguru |
| 配置 | Pydantic |
| 弹性 | 断路器 + 模型降级回退 |

## 致谢

本项目基于 [JC0v0/Customer-Agent](https://github.com/JC0v0/Customer-Agent) 进行优化开发，感谢原作者的开源贡献。

## License

MIT
