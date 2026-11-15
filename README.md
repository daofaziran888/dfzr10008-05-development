# 数据产品与API工厂

面向数据产品经理与开发者的数据产品快速构建平台。覆盖数据产品定义、API接口生成、数据模型封装、产品版本管理、调用配额控制、文档自动生成、产品市场分发与使用效果追踪的完整数据产品链路。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据产品定义 | `product` | 数据产品 | 产品编号、产品名称、产品类型 |
| API接口生成 | `apigen` | 接口配置 | 接口编号、接口名称、请求方法 |
| 数据模型封装 | `datapack` | 模型封装 | 封装编号、封装名称、原始数据源 |
| 产品版本管理 | `version` | 产品版本 | 版本编号、产品名称、版本号 |
| 调用配额控制 | `quota` | 配额配置 | 配置编号、调用方名称、每秒上限 |
| 文档自动生成 | `docgen` | 文档模板 | 模板编号、文档标题、目标接口 |
| 产品市场分发 | `market` | 市场商品 | 商品编号、商品名称、所属产品 |
| 使用效果追踪 | `usage` | 使用记录 | 记录编号、产品名称、调用方名称 |
| 产品沙箱测试 | `sandbox` | 沙箱实例 | 实例编号、测试产品、测试参数 |
| 数据管线编排 | `pipeline` | 管线节点 | 节点编号、节点名称、上游节点 |
| 数据筛选引擎 | `filter` | 筛选模板 | 模板编号、模板名称、目标数据源 |
| 场景化配置模板 | `scene` | 场景模板 | 模板编号、模板名称、适用场景 |
| 实时数据推送 | `realtime` | 推送通道 | 通道编号、通道名称、数据源名称 |
| 跨源数据关联 | `crossref` | 关联规则 | 规则编号、主数据源、关联数据源 |
| 数据质量反馈 | `feedback` | 反馈记录 | 反馈编号、数据产品、反馈类型 |
| 数据产品认证 | `badge` | 认证记录 | 认证编号、产品名称、认证类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 数据模型封装：多数据源批量构建与字段映射快照

数据模型封装页支持多选原始数据源后一次提交批量构建，覆盖「校验 → 裁决 → 隔离 → 发布 → 复用」全链路。

### 业务口径

1. **整组校验、逐条结果**：一次可勾选多个原始数据源（样例含 `orders`、`stores`、`members`），
   整组提交后按源逐条返回处理结果；每条字段映射先校验目标字段，再校验聚合规则，
   并核对来源字段是否真实存在。
2. **冲突裁决唯一口径**：同一输出字段被多个聚合规则写入时，按数据源提交顺序裁决，
   首个写法生效、其余写法列入冲突项（例如 `stores` 中 `store_level` 同时存在
   直接映射与维度关联取值两条规则）。冲突不会阻断构建，但必须显式列出。
3. **失败源隔离 + 单独重试**：校验失败的源单独标记为 `failed` 并从输出结构中隔离，
   其余源的模型封装仍进入「待发布」；失败项可在不影响同批其他源的情况下单独重新提交，
   仅失败项允许重试。
4. **发布冻结快照**：全部源通过后才能发布；发布时深拷贝冻结一份字段映射快照
   （含每个源的完整映射、裁决后输出结构与冲突记录），冻结后禁止再重试或废弃。
5. **接口配置侧读同一份快照**：接口配置（`apigen`）通过
   `/api/apigen/datapack-snapshots` 读取的就是封装发布时冻结的同一对象，
   保证两侧字段口径完全一致。
6. **同组重跑复用**：同一组数据源 + 相同映射口径按指纹（归一化映射的 SHA-256）复用，
   重复构建不会产生第二份版本；单独重跑其中一个源属于另一组数据源，不影响整组快照。

### 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/datapack/builds/sources` | 原始数据源目录（字段、支持的聚合规则、默认映射、样例数据） |
| POST | `/api/datapack/builds` | 多选数据源一次提交，整组校验并逐条返回结果 |
| GET | `/api/datapack/builds` | 构建记录列表 |
| GET | `/api/datapack/builds/{id}` | 构建结果：每源处理情况、冲突项、输出结构、快照 |
| POST | `/api/datapack/builds/{id}/retry` | 单条失败数据源重新提交 |
| POST | `/api/datapack/builds/{id}/publish` | 发布并冻结字段映射快照 |
| POST | `/api/datapack/builds/{id}/discard` | 废弃待发布构建（已发布不可废弃） |
| GET | `/api/apigen/datapack-snapshots` | 接口配置侧读取已发布冻结快照 |
| GET | `/api/apigen/datapack-snapshots/{snapshot_id}` | 读取单份快照 |

### 验证方式

```bash
# 后端单元测试（仅标准库 unittest，共 22 个用例）
cd backend && .venv/bin/python -m unittest tests.test_datapack_build

# 端到端验收脚本（需先启动后端，23 项检查逐条对应需求）
.venv/bin/python scripts/verify_datapack_build.py
```

领域：数据工程
子方向：数据分析与数据应用
