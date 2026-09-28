# WheelCAM 工作台界面优化（2026-09-28）

## 本次范围

优化 `services/wheelcam/workbench.html`，沿用工作台既有 API；没有修改建模、造型 Agent 或加工级 STEP 后端。

- 左侧：图片/文字依据、六项工程尺寸及来源、确认入口、版本关系。
- 中间：三维预览、透视/正面/侧面、适应窗口、线框、四阶段实际构建记录。
- 右侧：造型 Agent、校验、交付、参考对照。根据缺参和交付状态给出下一步入口。
- 明确区分用户提供尺寸、默认待确认尺寸、当前预览、修改前交付文件。
- 统一深色工程界面；窄屏调整排列；提供焦点状态、模型加载和失败重试。
- 请求期间禁用重复提交，切换版本时忽略迟到的响应，替换模型时释放旧几何和材质。

## 验收证据

本机启动：

```sh
PYTHONPATH=services .venv/bin/python -m wheelcam.workbench \
  --runs runs/spark-workbench-flow-20260928/acceptance --port 8793
```

使用上轮 Spark 真实产物，只读浏览，不修改这批证据。

| 检查 | 结果 |
|---|---|
| 桌面默认页面、真实 GLB | 正常显示三栏与模型 |
| 正面视角 | 已修正 CAD Z 到 glTF Y 的轴转换，显示轮毂正面 |
| 交付页及工程图入口 | 状态、下载链接、SVG 查看入口正常 |
| 缺参文字版本 | 显示 1/6 已确认，其余 5 项默认待确认 |
| 工程尺寸确认弹窗 | 正常打开，保留明确确认复选框；取消未写入 |
| 造型修改版本 | 提示交付待更新，未生成文件不显示下载链接 |
| 390×844 窄屏 | 模型优先，参数和 Agent 纵向排列，无横向挤压 |
| 浏览器控制台 | 本次检查未记录 error |
| JS 语法 | node --check 通过 |
| 自动回归 | tests/test_workbench.py + tests/test_privacy.py：12 passed |

实际浏览器截图（本地产物，不随 Git 提交）：

- `runs/workbench-design-20260928/01-before.png`
- `runs/workbench-design-20260928/02-workspace.png`
- `runs/workbench-design-20260928/03-mobile.png`

## 验收边界

本次验证前端呈现与只读交互，未重新发送真实模型请求或构建 CAD。本机预览服务没有配置模型端点；新建文字/对话功能需运行环境提供模型配置。本次 HTML 尚未部署至 Spark。此前 Spark 全链结果见 `spark-workbench-flow-evidence.md`。

GLB 仍为 L0 视觉草案；加工级 STEP 不包含完整造型曲面，未做结构仿真与制造审核，制造状态仍为 `not_released`。

## 第二轮：外圈盲窗与可用性修正

问题定位：Spark 图片链的 `delivery-4727f8143153` 配方为 `lip_pockets=0`；不是显示器材质问题。图片流程默认未运行造型 Agent，而 `wheel_skill.reconstruct()` 初始禁用外圈盲窗。文字链另一版设置了 15，因此外观不同。

本次新增：

- 预览区显示辐条组数、外圈盲窗目标数量、侧斜面宽度。
- 手动盲窗入口（0–40 整数）复用 `recipe_chat.apply_edit()` 与快照重建路径，无需语言模型。径向范围联动，工程尺寸不改变。
- `POST /api/runs/{name}/style` 保存独立子版本；旧配方 hash 拒绝，工程参数额外字段拒绝，失败不替换父版本。
- 来源记录为用户确认的造型目标及规则推导范围；深度保留模板假设。界面不把目标数量冒充自动识图或独立实测。
- `GET /api/capabilities` 仅报告模型是否配置，不暴露地址/密钥，也不将配置等同连通。未配置时禁用文字新建和对话，手动盲窗仍可用。
- URL 保留当前版本，刷新后仍显示同一款；版本读取失败恢复选中项。
- 压缩左侧参数区，确认按钮更易看到；状态横幅占据真实布局空间；显示手动修改记录。

实际浏览器操作验收在 `runs/workbench-refinement-20260928/demo-m59` 隔离副本完成，没有改写 Spark 评测目录：

- 手动 0 → 15，产生 `style-fb479ef7ad6e`，构建阶段 4.3 s。
- 配方实际变化仅 `lip_pockets` 与 `lip_pocket_r`，生成 15 个盲窗切削体。
- 单实体、外径 547.8 mm、总宽 294 mm、5 孔/PCD112、ET15、旋转对称：6 项均通过。
- 正面图可见外圈窗口，刷新后当前版本保留，控制台未记录 error。
- 截图：`runs/workbench-refinement-20260928/workspace-pockets.png`。

新增回归覆盖真实几何体积下降、尺寸锁定、径向范围联动、来源、旧版本冲突、非法输入、构建失败保留父版本及模型配置状态。此轮为 Mac 构建；尚未在 Spark 部署复测，未生成该造型子版本的下游交付包。盲窗属于视觉造型，仍不能声称完整造型已进入加工级 STEP。

第二轮自动验收命令：`PYTHONPATH=services .venv/bin/pytest -q tests/test_workbench_style.py tests/test_workbench.py tests/test_workbench_revision.py tests/test_privacy.py`，结果 **22 passed**（110.01 s）；JS 语法与 `git diff --check` 通过。

## 第三轮：恢复真实 Agent 对话与工程图审阅

### 已修复

上轮 8795 服务缺少 `WHEELCAM_CHAT_BASE_URL` / `WHEELCAM_CHAT_MODEL`，界面正确禁用了对话，但用户无法完成交互。本轮核对 Spark 模型列表后，通过仅绑定 localhost 的 SSH 转发接通 `step3-vl-10b-fp8`，重启原端口服务。未重启 Spark 模型。

实际浏览器在 `agent-live-check` 副本发送：“把窗口侧斜面宽度改成4毫米，保留15个外圈盲窗和所有工程尺寸。”真实模型返回 `flank_w: 2 → 4`，本机重建检查全部通过，历史保存在 `chat/history.json`，新预览在 `chat/01/`。原用户模型版本未修改。截图 `agent-connected.png`。

可重复启动（先停止占用工作台端口的旧服务）：

```sh
bash scripts/start_workbench_spark.sh runs/workbench-refinement-20260928 8795 18096
```

脚本只建立模型隧道并启动本机工作台，退出清理自己的隧道。模型在 Spark，几何构建在 Mac，不能称为 Spark 全链运行。

### 工程图与交互

- 三维工具栏直接进入工程图，交付页也有“放大审阅”入口。
- 大窗口按可用宽高适应整张 A3 图纸；50–300% 缩放，滚动查看细节，下载原始 SVG。
- 显示图纸所属版本、输入规格摘要、过期提醒及正视/剖面阅读指引。
- 无工程图时明确引导生成交付包，避免显示其他版本图纸。
- 正视投影不展示盲窗底面，界面明确要求结合三维造型核对。本轮未更改图纸生成几何或制造准备等级。
- 对话输入支持多行：Enter 发送，Shift+Enter 换行；中文输入法选词不触发发送。请求失败保留输入，真实回复后显示“模型已响应”。

浏览器验证：真实 Agent 编辑；现有 `delivery-4a052714a256` 图纸大窗口；100% → 125% → 适应窗口；刷新版本保持。工程图截图 `runs/workbench-refinement-20260928/drawing-viewer.png`。`tests/test_workbench.py` 和 `tests/test_privacy.py` 合计 12 passed；JS 与启动脚本语法检查通过。启动脚本本轮只做语法检查，实际服务通过等价 SSH 转发和启动命令运行。

## 第四轮：Studio 工作台视觉与专注视图

沿用石墨灰与暖金强调色，整理顶部项目标题、版本切换与主操作。三栏采用独立面板，工程尺寸改为六个紧凑卡片，折叠版本详情和手动盲窗控件，为 Agent 对话保留空间。模型背景与缎面材质统一；构建记录保持真实阶段结果。

新增纯预览控制：

- 缎面金属 / 灰模检查，不改变模型几何或任何工程参数。
- 专注模型：隐藏左右面板，点击“返回工作台”恢复；窄屏仍保留退出入口。

浏览器已验证桌面布局、390px 窄屏、材质切换、进入/退出专注视图、盲窗控件展开、工程图打开/关闭；未记录控制台 error。JS 语法检查通过。本轮没有更改后端或重新运行模型生成。截图：`runs/workbench-refinement-20260928/studio-workspace.png`。

## 独立 Agent 对话页

新增 `/agent?run=<版本>`，顶部主导航切换设计工作台与 Agent 对话。对话页采用参数上下文 / 宽幅对话 / 三维预览的布局，复用既有 API、历史和版本，避免两套状态分叉。任务运行中阻止页内导航跳转，完成后可返回同版本工作台。

验证：两条页面路由均 HTTP 200，JS 语法通过。浏览器在 `agent-live-check` 通过 Spark 真实模型询问盲窗数量，返回“15个”，`changed=None`、`dir=None`，未触发建模。截图 `runs/workbench-refinement-20260928/agent-page.png`。本次没有修改模型能力或 CAD 内核。

## 用户反馈调整：回归工作台

独立 Agent 页布局撤回，默认恢复模型居中、右侧 Agent 的 Studio 工作台。移除双页面导航；旧 `/agent` 链接打开相同工作台并保留 run 参数和历史。增加右侧“展开对话 / 收起对话”，仅调整栏宽，不调换模型与对话位置。浏览器验证旧链接回到工作台、历史保留以及展开/收起；未更改后端和模型。截图 `workbench-restored.png`。
