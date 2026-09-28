# 文字生成轮毂验收记录

2026-09-28。实现版本 `45a3e1e`。模型为 DGX Spark 上已有的 `step3-vl-10b-fp8`，经 SSH 隧道调用；模型服务未重启或改配置。Python、建模及产物位于 Mac 本机，**这里的建模耗时不是 Spark 建模耗时**。输出均为 L0 网格视觉草案，`manufacturing_status=not_released`。

| `docs/text-to-wheel.md` §7 | 结果 | 证据 |
|---|---|---|
| 1. 完整规格、5 辐直辐、15 盲窗、校验与速度 | 通过：六项规格均来自原文；5 辐、15 盲窗；所有网格检查通过；网格建模 1.30 s | `runs/text-eval-20260928/commit-45a3e1e-1/` 的配方、GLB、正视图和报告 |
| 2. 6 辐 Y 形、缺失规格 | 通过：21 寸和 6 辐来自原文；宽度、PCD、孔数、中心孔、ET、孔型标为默认或未知并逐项追问；网格检查通过；建模 1.14 s | `runs/text-eval-20260928/commit-45a3e1e-2/` |
| 3. 不支持的品牌仿造和扭转辐 | 通过：拒绝并说明模板范围 | `tests/test_text_wheel.py` |
| 4. 模型与原文 PCD 冲突 | 通过：以原文 114.3 mm 为准，记录拒绝的模型值 | `tests/test_text_wheel.py` |
| 5. 接加工级 STEP | 通过：单实体、STEP 回读等检查通过。完整文字演示链的重建、加工级 STEP、加工包、工程图 4/4 步成功，总计 39.5 s | `tests/test_text_wheel.py`；`runs/text-eval-20260928/chain-1/chain.json` |
| 6. 无模型服务测试与隐私测试 | 通过：`tests/test_text_wheel.py` 用假模型提案；连同工作台测试和 `tests/test_privacy.py` 共 15 项通过 | 本地 pytest 运行记录 |

模型提案可能把数值字段写成文字、选择不匹配预设或填入原文没有的尺寸。审核层拒绝这些提案，并从用户文字提取规格；报告中的 `rejected`、`clipped`、`questions` 和 `recipe_parameters` 保留决定依据。第一条真实模型运行记录有 3 个被拒绝的提案字段，第二条有 5 个。网格建模时间不含模型推理、渲染和导出；完整链总耗时见 `chain.json`。

`runs/` 是本机验收产物，不在 Git 提交里。复现模型验收时需设置 `WHEELCAM_CHAT_BASE_URL` 和 `WHEELCAM_CHAT_MODEL`，使用 `python -m wheelcam.text_wheel "描述" --out 新目录`。STEP 和参考 NC 仍需工程师审核，不能直接用于制造。
