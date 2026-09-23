# 许可证依赖冲突审计台（License Dependency Conflict Audit Console）

基于 **FastAPI + SQLite** 的轻量审计工具：上传包含包名、版本、许可证与依赖边的
manifest，系统规范化节点后依据内置许可证兼容矩阵计算**传递性**许可证约束，
标出每一处冲突的**最短依赖路径**，支持按包筛选，并可将完整结果导出为 JSON。

> ⚠️ 内置兼容矩阵是面向工程排查的常见许可证关系摘要，**不构成法律意见**。
> 矩阵可通过 `GET /api/licenses` 查阅，矩阵规则集中在 `app/licenses.py`，便于审计与替换。

---

## 功能特性

- **两种 manifest 格式**
  - 显式图：`nodes`（包名/版本/许可证）+ `edges`（依赖边）；
  - 包管理器风格：`packages` 列表/字典，每个包内联 `dependencies`（字符串或带元数据的对象）。
- **节点规范化**：许可证别名归一化（`gplv3` → `GPL-3.0`、`Apache 2.0` → `Apache-2.0` 等）；
  重复声明合并并告警；未声明的依赖引用自动补为**隐式节点（未知许可证）**并告警。
- **传递约束计算**：对每个可达的「消费方 → 传递依赖」有序对检查兼容性，
  使用 BFS 给出冲突的**最短依赖路径**（直接 / 传递分别标注），基于 Tarjan SCC 检测依赖环。
- **三类结论**：`conflict`（冲突）、`unknown`（许可证缺失/未识别，待人工确认）、`allow`。
- **按包筛选**：包名出现在路径任意位置 / 作为消费方 / 作为被依赖方 / 位于该包子树，
  并可叠加严重级别筛选。
- **JSON 导出**：完整报告（含规范化后的 manifest、分析结论、警告、筛选条件），
  支持附件下载。
- **持久化**：SQLite 三张表（manifests / nodes / conflicts），历史报告可查看、删除。
- **简洁中文网页**：上传、统计卡片、冲突表（高亮路径中的筛选包）、节点清单、依赖环、历史记录。

## 覆盖的许可证

公有领域、MIT、BSD-2/3-Clause、ISC、Apache-2.0、Unlicense（宽松许可）；
MPL-2.0、LGPL-2.1、LGPL-3.0（弱 copyleft）；GPL-2.0、GPL-3.0（copyleft）；
AGPL-3.0（网络 copyleft）；Proprietary（专有）。

矩阵内置若干典型判定，例如：

| 消费方许可证 | 依赖许可证 | 结论 | 说明 |
|---|---|---|---|
| Proprietary | GPL-2.0 / GPL-3.0 / AGPL-3.0 | 冲突 | 强 copyleft 与闭源分发不兼容 |
| GPL-2.0 | Apache-2.0 | 冲突 | Apache-2.0 专利终止条款与 GPL-2.0-only 不兼容 |
| GPL-2.0 | GPL-3.0 | 冲突 | 版本只可单向升级（反向组合允许） |
| GPL-3.0 | GPL-2.0 | 允许 | GPLv2 代码可升级并入 GPLv3 项目 |
| GPL-3.0 / AGPL-3.0 | Proprietary | 冲突 | copyleft 作品不能吸收专有代码 |
| AGPL-3.0 | LGPL-2.1 | 冲突 | 弱 copyleft 无法向 AGPL 升级组合 |
| 任意 | 未声明/未识别 | 待确认 | 不臆断，交人工审查 |

## 快速开始（Docker Compse，推荐）

```bash
docker compose up --build
```

容器启动时会**自动初始化 SQLite 数据库**（DDL 幂等），数据位于命名卷
`audit-data`（容器内 `/data/licenses.db`）。

访问地址：

- 审计网页（首页）：<http://localhost:8000/>
- 交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>
- ReDoc 文档：<http://localhost:8000/redoc>
- 健康检查：<http://localhost:8000/api/health>

停止：`docker compose down`（加 `-v` 同时删除数据卷）。

## 本地直接运行

需要 Python 3.11+：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 数据库路径可用环境变量覆盖（启动时自动建表）
export AUDIT_DB_PATH=./data/licenses.db
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

然后访问 <http://localhost:8000/>。

## Manifest 示例

显式图格式见 [`examples/manifest.sample.json`](examples/manifest.sample.json)，
包管理器风格见 [`examples/manifest.packages.json`](examples/manifest.packages.json)。

```json
{
  "name": "example-product",
  "nodes": [
    {"name": "app",  "version": "3.1.0", "license": "Proprietary"},
    {"name": "mid",  "version": "1.4.0", "license": "MIT"},
    {"name": "deep", "version": "0.9.0", "license": "GPL-3.0"}
  ],
  "edges": [
    {"from": "app", "to": ["mid"]},
    {"from": "mid", "to": ["deep"]}
  ]
}
```

该例会报告传递冲突：`app → mid → deep`（Proprietary 不能吸收 GPL-3.0），
并给出两跳的最短路径。

## JSON API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/manifests` | 上传 manifest（JSON 请求体，或 multipart 的 `file` 字段），立即分析并存库 |
| GET | `/api/manifests` | 历史报告列表，支持 `limit`、`offset`、`package` |
| GET | `/api/manifests/{id}` | 报告详情（manifest + report） |
| GET | `/api/manifests/{id}/findings` | 冲突列表，支持 `package`、`severity`、`scope` |
| GET | `/api/manifests/{id}/export` | 导出 JSON，支持 `?download=true` 附件下载 |
| DELETE | `/api/manifests/{id}` | 删除报告（级联删除节点与冲突行） |
| GET | `/api/licenses` | 许可证清单、分类与完整兼容矩阵 |
| GET | `/api/health` | 健康检查 |

### 命令行示例

```bash
# 上传并审计
curl -s -X POST http://localhost:8000/api/manifests \
  -H 'Content-Type: application/json' \
  --data @examples/manifest.sample.json

# 以文件方式上传
curl -s -X POST http://localhost:8000/api/manifests \
  -F "file=@examples/manifest.sample.json;type=application/json"

# 按包筛选（包出现在冲突路径任意位置）
curl -s "http://localhost:8000/api/manifests/1/findings?package=deep"

# 仅看冲突；限定包为被依赖方
curl -s "http://localhost:8000/api/manifests/1/findings?severity=conflict&scope=dependency"

# 导出（可叠加同样的筛选参数）
curl -s "http://localhost:8000/api/manifests/1/export?package=deep&download=true" -o report.json
```

`scope` 取值：`any`（默认，路径任意位置）、`consumer`、`dependency`、`subtree`
（消费方或被依赖方位于该包的依赖子树内）。

### 导出 JSON 结构

```json
{
  "exported_at": "2026-09-23T00:00:00+00:00",
  "tool": "license-dependency-audit",
  "manifest_id": 1,
  "manifest_name": "example-product",
  "created_at": "...",
  "manifest": { "name": "...", "nodes": [...], "edges": [...], "roots": [...], "warnings": [...] },
  "report": {
    "summary": {"total_nodes": 9, "total_edges": 9, "conflicts": 3, "unknown": 1, "cycles": 1},
    "findings": [
      {
        "consumer": "app", "dependency": "deep",
        "consumer_license": "Proprietary", "dependency_license": "GPL-3.0",
        "verdict": "conflict", "severity": "conflict",
        "reason": "Proprietary code cannot be combined with GPL-3.0 ...",
        "path": ["app", "mid", "deep"],
        "path_edges": [{"from": "app", "to": "mid"}, {"from": "mid", "to": "deep"}],
        "distance": 2, "transitive": true
      }
    ],
    "nodes": [...],
    "cycles": [...],
    "warnings": [...]
  }
}
```

## 项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由（网页 + JSON API）
│   ├── licenses.py      # 许可证归一化与兼容矩阵（单一事实来源）
│   ├── models.py        # manifest 解析与节点规范化
│   ├── analyzer.py      # 传递约束、BFS 最短路径、Tarjan 环检测、筛选
│   ├── db.py            # SQLite 初始化与持久化
│   └── static/
│       └── index.html   # 简洁中文单页前端（无外部依赖）
├── examples/            # 两种格式的示例 manifest
├── tests/               # 矩阵 / 解析 / 分析 / API 单元测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 运行测试

```bash
pip install -r requirements.txt httpx
python -m unittest discover -s tests -v
```

## 实现说明

- **最短路径**：以每个节点为源点做 BFS 得到前驱表，可在 O(N·(N+E))
  得到所有有序对的最短路径；含环图通过 visited 集合自然终止，不会死循环。
- **冲突方向**：检查的是“消费方许可证能否吸收被依赖方许可证”，对每个
  祖先→后代对都检查，因此既能发现直接边冲突，也能发现深层传递冲突。
- **未知不臆断**：许可证缺失或不在矩阵中时标记 `unknown`（待人工确认），
  不计为硬冲突，但会在统计与导出中单列。
