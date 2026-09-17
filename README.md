# 综合智能体集成服务

这是一个面向企业智能化场景的多智能体集成服务，展示不同执行模型如何通过同一个 Gateway 被客户端安全、稳定地使用，以及多种模型协同工作时需要处理的系统边界。

项目围绕统一接入、运行时编排、异步任务处理和制品交付进行设计，目录与模块职责经过梳理，依赖固定为 `agently==4.1.4.4`。工程使用的核心接口在 4.1.4.2—4.1.4.4 之间保持兼容，固定依赖版本以确保部署和验证结果稳定。

## 解决的核心问题

- **统一入口**：企业微信请求先归一为 `GatewayRequest`，再由模型意图或明确指令选择运行时。
- **统一事件**：Agently、问数 SSE 和 ACP 输出都转换为 `GatewayEvent`。
- **通用 Agent**：搜索、Browse、Skills、Actions、Workspace、Python 和 Shell 沙盒属于同一个 Agently Agent 运行时。
- **流程 Agent**：问数使用固定 TriggerFlow 流程，并作为可独立扩缩容的 HTTP/SSE 服务运行。
- **外部 Agent**：Codex 通过 ACP 接入，按 IM 会话隔离外部进程会话。
- **制品交付**：TaskWorkspace 保存任务文件，ArtifactStore 发布稳定制品，企业微信返回原生文件消息。
- **容量验证**：有界队列、Worker pool、503 背压和可调参数压测脚本。

## 项目结构

```text
integrated_agent_service/
├── integrated_agent/
│   ├── gateway/                 # 统一请求、事件、路由和会话选择
│   ├── runtimes/
│   │   ├── agent/               # Agently Agent + Actions + Skills + Sandbox
│   │   ├── question/            # 问数任务服务、TriggerFlow 和分析流程
│   │   └── acp/                 # Codex ACP client 与 session runtime
│   ├── storage/                 # 对外发布的制品
│   ├── transports/
│   │   ├── http/                # 问数 HTTP/SSE
│   │   └── wecom/               # 企业微信消息和文件
│   └── bootstrap/               # 两个可部署进程的依赖组装
├── data/                        # 问数演示数据库
├── skills/                      # 文档 Skill 包
├── static/                      # 简易 SSE Web 客户端
├── tests/                       # 单元、集成和端到端契约
├── run_server.py                # 问数 HTTP/SSE 服务
├── run_im_assistant.py          # 企业微信 Gateway
├── run_file_skill_demo.py       # 文件工作区离线运行入口
├── load_test.py                 # 参数化压力测试
└── ARCHITECTURE.md              # Owner、Node、Edge 与必要性账本
```

运行时生成的 `workspace/`、`logs/`、缓存和密钥文件不会进入版本库。

## 快速开始

### 1. 环境准备

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # macOS/Linux

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的配置
```

### 2. 启动问数服务

```bash
python run_server.py
# 服务启动在 http://127.0.0.1:8000
```

### 3. 启动企业微信助手

```bash
python run_im_assistant.py
# 需要配置 .env 中的 WECOM_BOT_ID 和 WECOM_BOT_SECRET
```

### 4. 运行测试

```bash
pytest tests/ -v
```

## 核心模块

### Gateway（统一网关）

所有外部请求通过 Gateway 统一接入，支持：
- 企业微信消息格式转换
- 意图识别和路由
- 会话管理和上下文维护

### Runtimes（运行时）

#### Agent Runtime
- 基于 Agently 框架
- 支持搜索、浏览、技能调用、文件操作
- Python/Shell 沙箱执行

#### Question Runtime（问数）
- 数据分析和查询服务
- 支持自然语言转 SQL
- 可视化图表生成
- 独立 HTTP/SSE 服务

#### ACP Runtime
- 外部 Codex 集成
- 会话隔离和管理
- 任务状态同步

### Transports（传输层）

#### HTTP
- 问数 API 服务
- SSE 流式响应

#### WeCom（企业微信）
- 消息接收和发送
- 文件和媒体处理
- 交互式卡片

## 技能（Skills）

项目内置多种文档处理技能：

- **文档转换**：DOCX、PDF、XLSX、PPTX 处理
- **简历优化**：简历审计和优化建议
- **Kami 排版**：专业文档排版（简历、一页纸、白皮书等）
- **Markdown 处理**：Markdown 转换和 PDF 生成

## 部署

### Docker 部署

```bash
docker build -t integrated-agent-service .
docker run -p 8000:8000 integrated-agent-service
```

### 生产环境

- 使用 Gunicorn + Uvicorn 运行 ASGI 应用
- 配置 Nginx 反向代理
- 设置日志和监控
- 配置数据库连接池

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | - |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `WECOM_BOT_ID` | 企业微信机器人 ID | - |
| `WECOM_BOT_SECRET` | 企业微信机器人密钥 | - |
| `QUESTION_SERVICE_URL` | 问数服务地址 | `http://127.0.0.1:8000` |
| `APP_HOST` | 应用监听地址 | `127.0.0.1` |
| `APP_PORT` | 应用监听端口 | `8000` |

### 模型配置

模型配置文件位于 `integrated_agent/model_settings.yaml`，可调整：
- 模型选择和参数
- 提示词模板
- 工具配置

## 开发指南

### 添加新技能

1. 在 `skills/` 目录创建新文件夹
2. 添加 `SKILL.md` 文件定义技能
3. 在 Gateway 中注册技能路由
4. 编写测试用例

### 添加新运行时

1. 在 `runtimes/` 目录创建新模块
2. 实现 `Runtime` 基类接口
3. 在 Gateway 中注册运行时
4. 配置意图识别规则

### 代码规范

- 使用 Python 3.10+ 语法
- 遵循 PEP 8 代码风格
- 使用 type hints
- 编写文档字符串
- 保持测试覆盖率 > 80%

## 架构设计原则

1. **单一职责**：每个模块只负责一个功能
2. **开闭原则**：对扩展开放，对修改关闭
3. **依赖倒置**：依赖抽象而非具体实现
4. **接口隔离**：保持接口小而专注
5. **最少知识**：模块间保持松耦合

## 性能优化

- 使用连接池管理数据库连接
- 实现请求缓存和响应缓存
- 支持异步处理和并发执行
- 配置负载均衡和自动扩缩容
- 监控系统性能指标

## 安全考虑

- API 密钥使用环境变量管理
- 实现请求频率限制
- 输入验证和清理
- SQL 注入防护
- XSS 攻击防护
- CSRF 攻击防护

## 故障排查

### 常见问题

1. **服务启动失败**
   - 检查端口是否被占用
   - 验证环境变量配置
   - 查看日志文件

2. **API 调用失败**
   - 检查网络连接
   - 验证 API 密钥
   - 确认请求格式

3. **数据库连接失败**
   - 检查数据库服务状态
   - 验证连接参数
   - 确认权限配置

### 日志查看

```bash
# 查看实时日志
tail -f logs/app.log

# 查看错误日志
grep "ERROR" logs/app.log
```

## 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

### 提交规范

使用 Conventional Commits 规范：

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `style:` 代码格式调整
- `refactor:` 代码重构
- `test:` 测试相关
- `chore:` 构建/工具相关

## 许可证

MIT License

## 联系方式

- 项目维护者：Gr-Air
- GitHub：https://github.com/Gr-Air/gr-dataagent
- 问题反馈：GitHub Issues

## 更新日志

详见 [CHANGELOG.md](CHANGELOG.md)
