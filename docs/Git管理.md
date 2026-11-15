# Git 源码管理

源码仓库：https://github.com/Jin-Yc/gina-sephora-updates 。`main` 分支管理 Python 源码、测试、文档、依赖清单和打包/发布脚本；Releases 存放用户 exe 和程序自动读取的更新包。

`.gitignore` 排除本地 Excel、CSV/TSV 业务数据、导出结果、虚拟环境、缓存、日志、凭据、`build/` 与 `dist/`。这些本地文件仍然保留，Git 不上传它们。测试使用内存样例，不依赖被排除的业务 Excel。

## 日常同步

在项目目录打开 PowerShell：

```powershell
git status
git diff
git add .
git diff --cached --stat
git commit -m "说明本次修改"
git push origin main
```

提交前查看暂存内容，确保只包含本次需要的源码和文档。`git push` 通过 Git Credential Manager 登录 GitHub，首次使用可能打开浏览器登录；不要将访问令牌写进项目文件或远程地址。

在其他开发电脑获取项目：

```powershell
git clone https://github.com/Jin-Yc/gina-sephora-updates.git
cd gina-sephora-updates
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

多台电脑协作时，开始修改前先执行 `git pull --ff-only`，避免覆盖其他电脑的提交。

## 源码同步与用户更新

提交或推送源码只更新仓库的 `main` 分支，不会自动让用户端升级。准备给用户发布时，提高 `version.py` 中的版本号，完成检查和测试，提交源码，再按 [自动更新.md](自动更新.md) 生成并发布 Release 更新包。

已有 `v2.1.1` Release 用于首轮自动更新验收，创建时仓库只有发布说明。首次补齐源码的提交位于其后的 `main` 分支；保留历史 tag，不重写已发布版本。后续发布时应从对应源码提交创建同名版本 tag，便于追溯。

完整 exe 保持在 Releases，不放入 Git 提交。普通业务代码变化时，用户仍使用原 exe 自动获取新版代码包。
