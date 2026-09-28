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
