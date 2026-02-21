# GitHub 上传完整指南

> 本指南适合Git新手，手把手教你如何将项目上传到GitHub
> 更新时间：2026-02-22

---

## 📚 准备工作

### 1. 安装Git (如果未安装)

**Windows:**
- 下载地址：https://git-scm.com/download/win
- 下载后安装，一路点击"Next"即可

**验证安装：**
```bash
git --version
```
如果显示版本号（如 `git version 2.x.x`），说明安装成功。

### 2. 注册GitHub账号

- 访问：https://github.com
- 点击右上角 "Sign up" 注册免费账号
- 记住你的用户名和邮箱

### 3. 配置Git用户信息（只需配置一次）

```bash
git config --global user.name "你的名字"
git config --global user.email "your_email@example.com"
```

---

## 🚀 开始上传（5个步骤）

### 步骤1：初始化本地Git仓库

```bash
# 进入项目目录
cd "D:\AI\my programs\fupan"

# 初始化Git仓库
git init

# 查看状态（此时应该显示有很多未跟踪的文件）
git status
```

**预期输出示例：**
```
On branch main

No commits yet

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        .env.example
        .gitignore
        AGENTS.md
        README.md
        ...（更多文件）
```

---

### 步骤2：添加文件到Git（暂存区）

```bash
# 添加所有文件（根据.gitignore规则）
git add .

# 再次查看状态（文件应该变绿色，显示"new file"）
git status
```

**注意：**
- `git add .` 会添加所有文件
- 但会自动跳过 `.gitignore` 中指定的文件
- 这时你应该看到 `.env` 和 `backup_*` 被忽略（红色显示）

---

### 步骤3：创建首次提交

```bash
# 提交文件（-m "提交信息"）
git commit -m "Initial commit: A股复盘系统"
```

**提交信息规范：**
- 英文：简单明了，如 "feat: 添加设置功能"
- 中文：也可以用中文，如 "初始提交：A股复盘系统"

**预期输出：**
```
[main (root-commit) abc1234] Initial commit: A股复盘系统
 XXX files changed, XXXXX insertions(+)
 create mode 100644 .gitignore
 create mode 100644 .env.example
 create mode 100644 README.md
 ...
```

---

### 步骤4：创建GitHub仓库

1. **登录GitHub**：https://github.com
2. **点击右上角 "+" → "New repository"**
3. **填写仓库信息：**
   - Repository name: `fupan`（仓库名称，建议用拼音或英文）
   - Description: `A股复盘系统 - 实时市场数据分析`
   - Public/Private:
     - ✅ **Public（公开）** - 推荐使用，免费，任何人都能看到代码
     - 🔒 Private（私有） - 需要付费，只有你能看到
   - ❌ **不要勾选** "Initialize this repository with a README"
     - （我们会上传自己的README.md）
4. **点击 "Create repository"**
5. **复制仓库URL**：
   - 格式：`https://github.com/你的用户名/fupan.git`
   - 例子：`https://github.com/johnsmith/fupan.git`

---

### 步骤5：连接并推送到GitHub

**执行以下命令（替换你的仓库URL）：**

```bash
# 添加远程仓库地址
git remote add origin https://github.com/你的用户名/fupan.git

# 查看远程仓库配置
git remote -v

# 将本地分支重命名为main
git branch -M main

# 推送到GitHub（首次推送）
git push -u origin main
```

**如果遇到认证问题：**
1. 会弹出GitHub登录窗口
2. 输入用户名和密码
3. 建议：使用 **Personal Access Token** 而不是密码
   - 创建Token：GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
   - 权限选择：`repo`（完整仓库权限）
   - 复制Token，在密码位置粘贴

**推送成功的标志：**
```
Enumerating objects: XXX, done.
Counting objects: 100% (XXX/XXX), done.
Writing objects: 100% (XXX/XXX), XX MiB | XX MiB/s, done.
Total XXX (delta XX), reused 0 (delta 0)
To https://github.com/你的用户名/fupan.git
 * [new branch]      main -> main
branch 'main' set up to track 'origin/main'.
```

---

## ✅ 上传成功后的检查

### 1. 在GitHub查看仓库

访问你的仓库地址：`https://github.com/你的用户名/fupan`

你应该看到：
- ✅ README.md 在仓库首页显示
- ✅ 所有代码文件都在
- ✅ 没有 `.env` 文件（已被忽略）
- ✅ 没有 `backup_*` 文件夹（已被忽略）
- ✅ database.md 在仓库显示

### 2. 查看提交历史

在GitHub仓库页面：
- 点击 "Commits" 标签
- 可以看到你的首次提交记录

---

## 🔄 日常使用Git（后续更新代码）

### 查看文件状态
```bash
git status
```

### 查看具体修改了什么
```bash
git diff                    # 查看未暂存的修改
git diff --staged           # 查看已暂存的修改
git diff 文件名              # 查看某文件的修改
```

### 添加修改并提交
```bash
# 添加修改的文件
git add .

# 提交修改
git commit -m "描述你的修改"

# 推送到GitHub
git push
```

### 拉取GitHub上的最新代码
```bash
git pull
```

---

## 🐛 常见问题解决

### Q1: 提交后想撤销怎么办？

**撤销未推送的提交：**
```bash
# 撤销最后一次提交，保留修改
git reset --soft HEAD~1

# 撤销最后一次提交，丢弃修改
git reset --hard HEAD~1
```

### Q2: 推送失败怎么办？

**错误信息：`Updates were rejected`**
```bash
# 强制推送（谨慎使用！）
git push -f origin main

# 或者先拉取远程修改再推送
git pull --rebase
git push
```

### Q3: 忘记添加.gitignore怎么办？

```bash
# 添加.git忽略规则后，需要手动清除已跟踪的文件
git rm --cached 文件名
git commit -m "update gitignore"
```

### Q4: 查看哪些文件被忽略了

```bash
# 查看被忽略的文件列表
git check-ignore -v *
```

---

## 📁 仓库结构参考

上传成功后，你的GitHub仓库应该是这样的：

```
fupan/
├── .gitignore              ✅ 已上传
├── .env.example            ✅ 已上传
├── .env                    ❌ 未上传（被忽略）
├── AGENTS.md               ✅ 已上传
├── GIT_UPLOAD_GUIDE.md     ✅ 已上传
├── README.md               ✅ 已上传
├── requirements.txt        ✅ 已上传
├── start.py                ✅ 已上传
├── data/
│   ├── database.py         ✅ 已上传
│   ├── fupan.db            ✅ 已上传
│   └── ...                 ✅ 其他文件
├── src/
│   ├── main.py             ✅ 已上传
│   ├── db_operations.py    ✅ 已上传
│   ├── data_acquisition.py ✅ 已上传
│   └── ...
├── templates/
│   └── dashboard.html      ✅ 已上传
├── static/
│   ├── chart.umd.min.js    ✅ 已上传
│   └── ...
└── backup_20260215_xxx/    ❌ 未上传（被忽略）
```

---

## 🎯 下一步

上传成功后，你可以：

1. **设置仓库描述和主题**
   - GitHub → Settings → Repository settings
   - 更新Description和Topics

2. **添加分支保护（可选）**
   - Settings → Branches → Add rule
   - 保护main分支，防止误删

3. **邀请协作者（可选）**
   - Settings → Manage access
   - Invite a collaborator

4. **使用GitHub Issues（问题追踪）**
   - Issues → New Issue
   - 记录bug或功能需求

5. **使用GitHub Wiki（文档）**
   - Wiki → Create first page
   - 编写详细的项目文档

---

## 💡 提示

1. **定期备份：**GitHub不是备份服务，重要数据仍需本地备份
2. **代码规范：**保持代码整洁，便于协作
3. **提交信息：**写有意义的提交信息，方便回溯
4. **隐私保护：**永远不要提交密码、密钥等敏感信息
5. **README维护：**及时更新README.md，让项目更易理解

---

**遇到问题？**
- GitHub官方文档：https://docs.github.com
- 本项目文件上传记录：查看 `GIT_UPLOAD_GUIDE.md`
- Git命令参考：https://git-scm.com/docs
