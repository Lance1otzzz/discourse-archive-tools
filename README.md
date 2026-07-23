# 水源帖子归档工具使用说明

这个工具用来归档水源社区里的这个事件线索帖：

```text
https://shuiyuan.sjtu.edu.cn/t/topic/494721
```

它会从这个帖子的主楼开始，继续寻找主楼里引用到的水源帖子，并把相关图片、附件一起下载下来。

## 会下载哪些内容

- 从 `494721` 的主楼开始。
- 如果遇到 `/t/topic/491016` 这种链接，会下载该帖主楼。
- 如果遇到 `/t/topic/491051/906` 这种链接，会下载该帖第 `906` 楼。
- 每下载一个帖子，都会继续检查里面有没有新的水源帖子链接。
- 帖子里的水源图片、头像、附件也会下载。
- 只能下载你的账号本来能看的内容，不会绕过权限。

## 先确认你用的是什么终端

不同终端设置环境变量的命令不一样。你只需要看自己正在用的那一类：

- Linux/macOS 的 bash 或 zsh
- fish
- Windows PowerShell，也就是 `pwsh` 或 PowerShell
- Windows 命令提示符，也就是 `cmd`

如果你不知道自己是哪一种，看终端窗口标题，或者直接问旁边懂电脑的人。

## 第一步：进入工具目录

Linux/macOS/bash/zsh/fish：

```bash
cd ~/discourse_archive_tools
```

PowerShell：

```powershell
cd ~/discourse_archive_tools
```

cmd：

```cmd
cd %USERPROFILE%\discourse_archive_tools
```

如果你的工具目录不在这个位置，把上面的路径换成实际路径。

## 第二步：准备环境

这个工具只有一个第三方包：`cryptography`，它只用于申请水源 User API Key。

如果你已经安装了 `uv`，推荐执行：

```bash
uv sync
```

如果你没有 `uv`，直接用 pip 安装依赖：

```bash
python -m pip install cryptography
```

如果你的电脑不认 `python`，但认 `python3`，就执行：

```bash
python3 -m pip install cryptography
```

Windows 上如果 `python` 不可用，但 `py` 可用，就执行：

```cmd
py -m pip install cryptography
```

这一步只需要成功执行一次。以后再运行工具时可以跳过。

后面的命令里：

- 有 `uv`：使用 `uv run python ...`
- 没有 `uv`：把 `uv run python` 换成 `python`
- 如果你的系统只认 `python3`：把 `uv run python` 换成 `python3`
- Windows 如果只认 `py`：把 `uv run python` 换成 `py`

## 第三步：申请水源 User API Key

在工具目录里执行下面这一行。这个命令在 bash、zsh、fish、PowerShell、cmd 里都可以用：

```bash
uv run python request_user_api_key.py --site-url https://shuiyuan.sjtu.edu.cn --application-name "Personal Discourse Archive" --scopes read
```

没有 `uv` 的话，执行：

```bash
python request_user_api_key.py --site-url https://shuiyuan.sjtu.edu.cn --application-name "Personal Discourse Archive" --scopes read
```

然后按下面步骤操作：

1. 终端会打印一个很长的链接。
2. 复制这个链接。
3. 在浏览器里打开它。
4. 确认你已经登录水源。
5. 点击授权。
6. 页面会显示一段很长的 `payload`。
7. 把这段 `payload` 复制回终端，按回车。

成功后，终端会打印类似：

```text
client_id=...
key=...
```

`key=` 后面的内容就是你的水源 API Key。

注意：不要把 API Key 发给别人。如果不小心泄露了，去水源“偏好设置 -> 安全性”页面撤销它，然后重新申请。

## 第四步：把 API Key 放进当前终端

下面四组命令只选一组，按你的终端类型执行。

### bash 或 zsh

```bash
read -rsp "Discourse User API key: " DISCOURSE_USER_API_KEY
export DISCOURSE_USER_API_KEY
printf '\n'
```

### fish

```fish
read -s -gx -P "Discourse User API key: " DISCOURSE_USER_API_KEY
echo
```

### PowerShell / pwsh

```powershell
$env:DISCOURSE_USER_API_KEY = Read-Host "Discourse User API key"
```

### cmd

```cmd
set /p DISCOURSE_USER_API_KEY=Discourse User API key: 
```

执行后，把刚才 `key=` 后面的内容粘贴进去，按回车。

说明：PowerShell 和 cmd 里输入时可能会显示出来，注意不要录屏或发给别人。

## 第五步：先小规模测试

建议先跑一个小测试，确认能正常下载。

### bash、zsh 或 fish

有 `uv` 时可以用快捷脚本：

```bash
MAX_PAGES=50 OUT_DIR=~/shuiyuan_topic_494721_test ./archive_shuiyuan_494721.sh
```

没有 `uv` 时，不要用上面的 `.sh` 脚本，直接执行：

```bash
env PYTHONUTF8=1 python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out ~/shuiyuan_topic_494721_test --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --max-pages 50
```

如果你的系统只认 `python3`，把上面的 `python` 换成 `python3`。

### PowerShell / pwsh

```powershell
$env:PYTHONUTF8 = "1"
uv run python .\discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "$HOME\shuiyuan_topic_494721_test" --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --max-pages 50
```

没有 `uv` 时，把第二行开头的 `uv run python` 换成 `python`：

```powershell
$env:PYTHONUTF8 = "1"
python .\discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "$HOME\shuiyuan_topic_494721_test" --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --max-pages 50
```

### cmd

```cmd
set PYTHONUTF8=1
uv run python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "%USERPROFILE%\shuiyuan_topic_494721_test" --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --max-pages 50
```

没有 `uv` 时，把第二行开头的 `uv run python` 换成 `python`：

```cmd
set PYTHONUTF8=1
python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "%USERPROFILE%\shuiyuan_topic_494721_test" --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --max-pages 50
```

测试结果目录：

```text
shuiyuan_topic_494721_test
```

如果没有明显报错，就可以跑完整归档。

## 第六步：运行完整归档

### bash、zsh 或 fish

有 `uv` 时可以用快捷脚本：

```bash
./archive_shuiyuan_494721.sh
```

没有 `uv` 时，直接执行：

```bash
env PYTHONUTF8=1 python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out ~/shuiyuan_topic_494721_main_posts_recursive_archive --max-depth 2 --first-post-only --topic-links-only --delay 1.5
```

如果你的系统只认 `python3`，把上面的 `python` 换成 `python3`。

### PowerShell / pwsh

```powershell
$env:PYTHONUTF8 = "1"
uv run python .\discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "$HOME\shuiyuan_topic_494721_main_posts_recursive_archive" --max-depth 2 --first-post-only --topic-links-only --delay 1.5
```

没有 `uv` 时，把第二行开头的 `uv run python` 换成 `python`：

```powershell
$env:PYTHONUTF8 = "1"
python .\discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "$HOME\shuiyuan_topic_494721_main_posts_recursive_archive" --max-depth 2 --first-post-only --topic-links-only --delay 1.5
```

### cmd

```cmd
set PYTHONUTF8=1
uv run python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "%USERPROFILE%\shuiyuan_topic_494721_main_posts_recursive_archive" --max-depth 2 --first-post-only --topic-links-only --delay 1.5
```

没有 `uv` 时，把第二行开头的 `uv run python` 换成 `python`：

```cmd
set PYTHONUTF8=1
python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "%USERPROFILE%\shuiyuan_topic_494721_main_posts_recursive_archive" --max-depth 2 --first-post-only --topic-links-only --delay 1.5
```

默认结果目录：

```text
shuiyuan_topic_494721_main_posts_recursive_archive
```

中途断了也没关系，重新执行同一个完整归档命令即可续跑。

## 想递归更深怎么办

默认递归深度是 `2`。想更深就把命令里的 `--max-depth 2` 改成更大的数字。

例如 PowerShell/cmd 里的完整命令，把：

```text
--max-depth 2
```

改成：

```text
--max-depth 5
```

bash、zsh 或 fish 可以这样：

```bash
MAX_DEPTH=5 ./archive_shuiyuan_494721.sh
```

没有 `uv` 时，不用 `.sh` 脚本；在完整归档命令里把 `--max-depth 2` 改成 `--max-depth 5`。

数字越大，下载越多，耗时越久。

## 如果图片下载失败

有些水源图片可能还需要浏览器登录态。如果只用 API Key 时图片报错，可以导出浏览器 cookies。

bash、zsh 或 fish，有 `uv` 时：

```bash
COOKIES_FILE=~/Downloads/shuiyuan-cookies.txt ./archive_shuiyuan_494721.sh
```

没有 `uv` 时：

```bash
env PYTHONUTF8=1 python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out ~/shuiyuan_topic_494721_main_posts_recursive_archive --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --cookies ~/Downloads/shuiyuan-cookies.txt
```

如果你的系统只认 `python3`，把上面的 `python` 换成 `python3`。

PowerShell / pwsh：

```powershell
uv run python .\discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "$HOME\shuiyuan_topic_494721_main_posts_recursive_archive" --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --cookies "$HOME\Downloads\shuiyuan-cookies.txt"
```

没有 `uv` 时，把开头的 `uv run python` 换成 `python`。

cmd：

```cmd
uv run python discourse_archiver.py "https://shuiyuan.sjtu.edu.cn/t/topic/494721" --root "https://shuiyuan.sjtu.edu.cn" --out "%USERPROFILE%\shuiyuan_topic_494721_main_posts_recursive_archive" --max-depth 2 --first-post-only --topic-links-only --delay 1.5 --cookies "%USERPROFILE%\Downloads\shuiyuan-cookies.txt"
```

没有 `uv` 时，把开头的 `uv run python` 换成 `python`。

把 `shuiyuan-cookies.txt` 换成你实际导出的 cookies 文件路径。

如果不知道怎么导出 cookies，可以先不用这一步；只有图片下载失败时再处理。

## 下载完后看哪里

归档目录里主要有这些文件夹和文件：

```text
topics/
assets/
archive_index.jsonl
summary.json
```

含义如下：

- `topics/`：保存下来的水源帖子内容。
- `topics/<topic_id>/posts/*.html`：帖子 HTML 内容，直接看这个最方便。
- `topics/<topic_id>/posts/*.json`：帖子原始 API 数据。
- `assets/`：下载到的图片、头像和附件。
- `archive_index.jsonl`：下载日志。
- `summary.json`：本次下载统计。

## 常见问题

### 提示 `read: -rsp: 未知选项`

说明你不是 bash/zsh，多半是 fish。请用 fish 版本命令：

```fish
read -s -gx -P "Discourse User API key: " DISCOURSE_USER_API_KEY
echo
```

### 提示没有 API Key

说明你还没有把 API Key 放进当前终端。重新执行第四步。

### 运行到一半断了

直接重新运行同一个归档命令。工具会尽量续跑。

### 下载很慢

这是正常的。脚本默认会放慢请求速度，避免给水源服务器造成压力。

### 我只想抓起始帖，不想递归

bash、zsh 或 fish，有 `uv` 时：

```bash
MAX_DEPTH=0 ./archive_shuiyuan_494721.sh
```

没有 `uv` 时，以及 PowerShell/cmd：把完整归档命令里的 `--max-depth 2` 改成 `--max-depth 0`。

### 我不小心泄露了 API Key

去水源“偏好设置 -> 安全性”页面撤销这个 key，然后重新申请一个。
