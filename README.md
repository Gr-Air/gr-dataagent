# 问数智能体服务

自然语言驱动的数据分析服务。用户提出业务问题，系统自动完成问题改写、SQL 生成、执行、结果格式化和最终回答的全流程。

## 核心流程

```
用户问题
  → rewrite_question    问题改写（LLM + Pydantic 验证）
  → generate_sql        SQL 生成（动态目录 + 2 次重试）
  → execute_sql         SQL 执行（安全检查 + 只读连接）
  → normalize_results   结果格式化（列语义推断 + 单位换算）
  → compose_final_answer 最终回答（双 Agent 校验 + 证据引用）
```

## 五阶段详解

### 1. rewrite_question — 问题改写

- 将用户问题拆解为结构化子问题（Requirement + AnalysisTarget）
- Pydantic 模型验证输出格式（唯一性、引用完整性）
- `to_rewrite_result()` 应用业务规则补充指标/维度
- 输出：`subquestions` 列表，传入下一阶段

### 2. generate_sql — SQL 生成

- **三层目录架构**：Full catalog → Rewrite catalog → SQL catalog
- SQL catalog 按子问题智能裁剪（~3000 tokens → ~500 tokens，节省 83%）
- `_select_sql_tables()` 智能选表：精确匹配 → 贪心覆盖 → 维度匹配
- **2 次重试机制**：失败后传入修复提示（`repair={previous_sql, issues}`）
- 预检（`validate_sql`）+ 语义检查（`_semantic_sql_issues`）
- 失败任务标记 `status="failed"`，不阻塞其他子问题

### 3. execute_sql — SQL 执行

- SQL 安全检查（`FORBIDDEN_SQL` 正则）
- 只读连接（`mode=ro` + `PRAGMA query_only=ON`）
- 输出列验证（与预期列对比）
- 结果截断（`ROW_LIMIT=200`）
- 结果保存到文件，返回 `result_ref` 引用

### 4. normalize_results — 结果格式化

- `infer_column_semantics()` 推断列语义（ratio/currency/duration/plain）
- `format_evidence_value()` 格式化显示值（单位换算：分→元/万元）
- 关联子问题信息，生成 evidence 块
- 只传摘要 + 展示行给最终回答（~100 tokens/块，不传原始数据）

### 5. compose_final_answer — 最终回答

- 主回答 Agent 生成草稿（`draft_answer`）
- 校验 Agent 审查并修正（`answer`）
- 验证证据引用完整性（`evidence_ids` 必须有效）
- `_append_missing_gross_profit()` 补充毛利核对
- 失败兜底：生成默认答案，不会完全失败
- 输出：`answer`（总结）、`claims`（结论 + 证据引用）、`limitations`（未覆盖目标）

## 鲁棒性设计

| 机制 | 说明 |
|------|------|
| SQL 2 次重试 | 第一次失败后传入修复提示，让 LLM 修正 SQL |
| 多层 SQL 检查 | 预检（语法/安全）+ 语义检查（业务逻辑） |
| 失败标记与跳过 | 失败子问题标记 `failed`/`skipped`，不阻塞其他 |
| 双 Agent 校验 | 主回答生成草稿，校验 Agent 审查修正 |
| 三级状态管理 | `completed`（全部成功）/ `partial`（部分失败）/ `failed`（最终失败） |
| 失败兜底 | 最终阶段完全失败时生成默认答案 |

## TraceLog 可观测性

- **业务事实**（`business.*`）：问题改写、SQL 生成、执行、最终回答
- **框架事实**（`framework.*`）：模型调用、token 用量、耗时
- `value_shape()` 只记录数据形状，不记录值
- 保存 3 个文件：`run.json`（完整数据）、`events.jsonl`（事件流）、`summary.json`（摘要视图）
- 6 种视图：task / stage / operation / attempt / sql / evidence

## 并发控制

- **任务级**：`worker_count=4`，多个任务并行处理
- **子问题级**：`for_each(concurrency=4)`，子问题并行生成 SQL 和执行
- **结果累积**：`async_append_state` 自动累积并行结果

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python run_server.py
# http://127.0.0.1:8000

# 运行测试
pytest tests/ -v
```

## 项目结构

```text
integrated_agent/runtimes/question/
├── worker.py                  # 外层任务管理（TriggerFlow）
├── analysis/
│   ├── workflows/
│   │   ├── main_flow.py       # 内层业务流程（5 阶段）
│   │   └── chunks/
│   │       ├── rewrite.py     # 问题改写
│   │       ├── generate_sql.py # SQL 生成
│   │       ├── execute_sql.py # SQL 执行
│   │       ├── normalize.py   # 结果格式化
│   │       └── final_answer.py # 最终回答
│   ├── prompts/               # LLM 提示词
│   ├── utils/
│   │   ├── catalog.py         # 三层目录构建
│   │   └── database.py        # SQLite 交互
│   └── trace_log.py           # 可观测性
├── models.py                  # Pydantic 数据模型
├── stores.py                  # 内存存储（事件、任务）
└── service.py                 # 任务服务
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 |
| `APP_HOST` | 监听地址（默认 `127.0.0.1`） |
| `APP_PORT` | 监听端口（默认 `8000`） |

## 许可证

MIT License
