# CAD Agent 后端评估

更新日期：2026-09-22。

## 结论

WheelCAM 保留自己的 `wheel-agent-cad-plan-v1` 作为唯一写入边界。MiMo 负责看图、判断拓扑、选择工具和提出参数候选；
CadQuery/OpenCASCADE、未来的 typed FreeCAD MCP 或 Zoo KCL adapter 负责确定性执行。任何后端都不能绕过 revision、关键参数审批、
B-Rep/STEP 回读和 `NOT RELEASED` 状态。

当前优先级：

1. **现有 CadQuery/OpenCASCADE adapter**：主执行器，已在本机通过回归与 STEP 检查。
2. **typed FreeCAD MCP**：候选实验执行器；优先选择显式建模操作、事务、检查与回滚，不开放任意 Python。
3. **GenCAD**：复用截面渲染与 build-render-inspect 思路；其 `freecad_run` 任意脚本接口不直接暴露给模型。
4. **FreeCAD Engineering skills**：复用参数可编辑性、命名特征、重算/保存/重开验证和 `not_tested` 规则；Skill 本身不是 CAD 内核。
5. **Zoo MCP / Zookeeper**：远程 KCL/STEP 候选与对照后端。需要单独 Zoo Token，托管操作计入 API 用量，不作为免费默认主线。

本机探测未发现 FreeCAD、FreeCADCmd 或 freecad-mcp，因此本轮没有安装或声称验证 FreeCAD 执行器。

## Agent 闭环

```text
MiMo 2.6 Pro 看图/读取工程状态
        ↓
wheel-agent-cad-plan-v1
        ↓
参数候选 / UNKNOWN / request_tool
        ↓
人工批准关键变更
        ↓
CadQuery adapter（当前）
FreeCAD typed MCP adapter（候选）
Zoo KCL adapter（可选远程对照）
        ↓
B-Rep + STEP readback + canonical render
        ↓
轮廓/特征误差与失败诊断
        └────────→ 下一轮 Agent 计划
```

语言模型不得直接输出高密度 `window_outlines_mm`。窗口草图由标注/视觉拟合工具产生；Agent 只能请求
`analyze_primary_image`、`fit_window_sketch` 或 `compare_latest_build` 等注册工具。未来 FreeCAD/Zoo 操作也必须先映射为
类型化 CAD IR，不接受 Python、shell 或未审查 KCL 直接覆盖主模型。

## 当前原图实测

MiMo 2.6 Pro 看图后判断为 5 组 Y 形辐条与 5 个螺栓孔，请求主图分析和窗口拟合，并把背面、厚度、PCD、孔径、
中心孔与槽深保留为 UNKNOWN。通用识图返回 7 组但状态为 `ambiguous / can_apply=false`，已拒绝；专用窗口拟合确认
5 组、每组 3 个窗口，留出组 IoU 为 0.9617，但候选 `window_outlines_mm` 与 revision 19 当前草图完全相同。

因此当前视觉差距不是二维轮廓重描问题，而是 CAD IR 尚未表达足够的三维辐条 Design Intent：双支臂截面、凸脊、凹槽、
根部融合、轮辋端过渡和轴向曲率。下一阶段应先增加这些类型化特征，再让 Agent 调参和闭环比较；单纯更换 CAD 执行器不会
自动恢复这些不可观测或未建模的结构。

## 参考实现

- FreeCAD Engineering skills: <https://github.com/V0v1kkk/freecad-engineering>
- typed FreeCAD MCP research: <https://github.com/yonosoft/freecad-mcp>
- GenCAD: <https://github.com/gurul/gencad>
- Zoo MCP: <https://zoo.dev/docs/developer-tools/mcp>
- Zoo Zookeeper Agent API: <https://zoo.dev/docs/developer-tools/agent-api>
