# WheelCAM Demo 视频

约 109 秒，1920×1080、30 fps、H.264，中文配音，无旁白字幕；标题、参数和原界面文字保留。此版本是基于真实截图和产物的动画讲解片，**不是连续实时录屏**。

从仓库根目录重新生成：

```sh
bash scripts/render_demo_video.sh
```

需要 macOS 的 `say` 中文声线、FFmpeg/FFprobe、Node、npm、Python 3，以及本地已有 `runs/workbench-hardening-20260928` 取证素材。素材不随 Git 提交；`scripts/prepare.py` 只复制明确列出的 7 个文件并生成来源 SHA-256 清单，不读取环境文件或整目录打包。

输出：`runs/demo-video/wheelcam-demo-zh-1080p.mp4`。

预览 / 编辑：

```sh
cd demo-video
python3 scripts/prepare.py
python3 scripts/voiceover.py
npm ci
npx remotion studio src/index.ts --no-open
```

## 分镜

旁白源稿在 `scripts/narration.json`。7 段依次为：产品定位、工程理解、照片修正、连续对话、Spark 整链、工程交付、审核边界。分镜时长根据实际合成音频自动计算，保留段前后停顿。

默认本机 Tingting 声线，文本不上传云服务。可通过 `DEMO_VOICE` / `DEMO_VOICE_RATE` 更换声线和语速，或替换 `public/voice/*.wav` 并同步 `src/voice.json` 的时长。

每个分镜在 `src/scenes/` 独立编辑，旁白稿与画面标题分离；`subtitle` 文稿仍保留在源码供后续加字幕，当前不显示。场景时长来自 `src/voice.json`。正式 4 分钟口播脚本仍为 `docs/demo-video-script.md`；本片是约 109 秒中文配音精简版。

所有照片和 CAD 都来自既有开发案例。影片不声称新款泛化、完整造型 STEP 或制造批准。没有加入无 Skill 对照胜率和未经测量的人工效率数字。
