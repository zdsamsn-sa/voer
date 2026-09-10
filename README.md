# Voer.host 免费服务器会话续期

免费档服务器是**会话制**（默认约 4 小时），续期必须看完 **3 个 Google 激励广告**，每次成功 **+4 小时**。

**平台限制：**

| 限制 | 说明 |
|------|------|
| 每 UTC 日最多 | **4 次** |
| 每个会话最多 | **4 次** |
| 每次成功 | **+4 小时** |
| 广告播放 | 必须真实播放（`headless: false` + 虚拟显示 xvfb），否则不发奖励 |

本项目提供：

1. **本地 / VPS** 直接运行的 Python 脚本（Playwright）
2. **检测关机并自动开机**（若需看广告则自动模拟观看）
3. **会话续期**（看 3 个激励广告，+4 小时）
4. **GitHub Actions** 定时/手动运行（推荐）
5. **Telegram** 结果通知 + 面板截图（可选）

---

## 目录

1. [需要的环境变量](#一需要的环境变量最重要)
2. [如何获取 VOER_SERVER_ID 和 VOER_TOKEN](#如何获取-voer_server_id-和-voer_token)
3. [Telegram 通知 + 截图（可选）](#二telegram-通知--截图可选)
4. [GitHub Actions 自动续期](#三github-actions-自动续期推荐)
5. [本地 / VPS 直接运行](#四本地--vps-直接运行)
6. [定时任务示例](#五定时任务示例)
7. [常见问题排查](#六常见问题排查)
8. [文件说明](#七文件说明)
9. [安全建议](#八安全建议)

---

## 一、需要的环境变量（最重要）

脚本**优先读取环境变量**，没有时才读本地 `config.json`。

| 环境变量 | 是否必须 | 说明 |
|----------|----------|------|
| `VOER_SERVER_ID` | **必须** | 服务器 UUID |
| `VOER_TOKEN` | **必须** | 登录 Cookie 中的 JWT（约 7 天有效） |
| `TELEGRAM_BOT_TOKEN` | 可选 | Telegram 机器人 Token，用于通知 |
| `TELEGRAM_CHAT_ID` | 可选 | Telegram 聊天 / 群组 ID |
| `VOER_ADS_PER_EXTENSION` | 可选 | 每次需要的广告数，默认 `3` |
| `VOER_AD_DURATION_SEC` | 可选 | 单个广告等待秒数，默认 `32` |

本地也可用 `config.json`（由 `config.example.json` 复制），但**环境变量优先级更高**，适合 CI / Docker / cron。

---

### 如何获取 VOER_SERVER_ID 和 VOER_TOKEN

#### 1. 获取 `VOER_SERVER_ID`

1. 浏览器打开并登录 [https://voer.host](https://voer.host)
2. 进入你的服务器面板
3. 看地址栏，类似：

   ```text
   https://voer.host/panel/server/58d72957-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

4. **复制 `/panel/server/` 后面那一整串 UUID** → 这就是 `VOER_SERVER_ID`

#### 2. 获取 `VOER_TOKEN`

1. 在已登录的 voer.host 页面
2. 按 **F12** 打开开发者工具
3. 顶部点 **Application**（中文可能叫「应用」）
4. 左侧 **Cookies** → 点击 `https://voer.host`
5. 找到 **Name = `token`** 那一行
6. **完整**复制 **Value**（很长，以 `eyJ` 开头，中间有两个 `.`，共三段）
7. 不要带引号、空格、换行

> **注意：** token 大约 **7 天**过期。过期后脚本会报 **401 Unauthorized**，重新按上面步骤复制并更新 Secret / 环境变量即可。

---

## 二、Telegram 通知 + 截图（可选）

续期**成功 / 失败 / 异常**后，可自动把结果和面板截图发到 Telegram。

不配置也不影响续期，脚本会正常跑，只是不发通知。

### 1. 创建 Bot

1. Telegram 搜索 **@BotFather**
2. 发送 `/newbot`，按提示起名
3. 完成后得到 Token，形如：

   ```text
   7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxx
   ```

4. 这就是 `TELEGRAM_BOT_TOKEN`

### 2. 获取 Chat ID

**发给自己（私聊）：**

1. 在 Telegram 里找到刚创建的 Bot，点 **Start**
2. 浏览器打开：

   ```text
   https://api.telegram.org/bot<你的Token>/getUpdates
   ```

3. 在返回 JSON 里找 `"chat":{"id": 123456789}`  
   这个数字就是 `TELEGRAM_CHAT_ID`

**发到群组：**

1. 把 Bot 拉进群，并在群里随便发一条消息
2. 同样打开上面的 `getUpdates` 链接
3. 群的 `chat.id` 一般是**负数**（如 `-1001234567890`）

### 3. 配置方式

**GitHub Actions：**  
仓库 → Settings → Secrets and variables → Actions → 新增：

| Name | 值 |
|------|-----|
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 Token |
| `TELEGRAM_CHAT_ID` | 上面的数字 ID |

**本地 / VPS：**

```bash
export TELEGRAM_BOT_TOKEN='7123456789:AAH...'
export TELEGRAM_CHAT_ID='123456789'
```

或在 `config.json` 中填写：

```json
"telegram_bot_token": "7123456789:AAH...",
"telegram_chat_id": "123456789"
```

### 4. 通知内容示例

**成功：**

```text
✅ Voer 续期成功
服务器: 8d8c0477…
原到期: 2026-09-10T06:56:51.000Z
新到期: 2026-09-10T10:56:51.000Z
累计续期: 1
今日续期: 1
```

并附带一张面板截图。

**失败 / 未生效：** 会说明可能原因（找不到按钮、今日已达上限等），并尽量附截图。

---

## 三、GitHub Actions 自动续期（推荐）

适合没有长期开机的机器，或想省事的人。

### 3.1 准备仓库

1. 把本项目整个文件夹上传到你自己的 **GitHub 私有仓库**（强烈建议 Private）
2. 结构应类似：

   ```text
   .
   ├── .github/workflows/voer-renew.yml
   ├── voer_renew.py
   ├── requirements.txt
   ├── config.example.json
   ├── .gitignore
   └── README.md
   ```

### 3.2 添加 Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Name | 是否必须 | 说明 |
|------|----------|------|
| `VOER_SERVER_ID` | 必须 | 服务器 UUID |
| `VOER_TOKEN` | 必须 | JWT token |
| `TELEGRAM_BOT_TOKEN` | 可选 | TG 通知 |
| `TELEGRAM_CHAT_ID` | 可选 | TG 通知 |

**永远不要**把真实 token 写进代码或提交到 git。

### 3.3 启用并测试

1. 打开仓库 **Actions** 标签
2. 左侧选择 **Voer.host 会话续期**
3. 点 **Run workflow**
4. 选择模式：
   - `status`：只查看状态（**不消耗广告**）
   - `power-on`：只尝试开机（关机时；可能需要看广告）
   - `renew`：若关机则开机，再看广告做会话续期（默认推荐）
5. **第一次务必先跑 `status`**，确认 Secrets 正确、token 未过期
6. 再跑 `renew`，等待约 3～5 分钟

成功日志大致类似：

```text
[xx:xx:xx] token 诊断: 长度=224, 段数=3, …【未过期，剩余约 167 小时】
[xx:xx:xx] 当前到期: … | 已续期: 0 | 今日: 0
[xx:xx:xx] 已点击续期入口: …
[xx:xx:xx] 已点击第 1/3 个 Watch ad …
[xx:xx:xx] 续期成功 -> 新到期: … | 累计: 1 | 今日: 1
[xx:xx:xx] Telegram 截图已发送
```

### 3.4 定时规则

默认工作流：

```yaml
schedule:
  - cron: "0 0,8,16 * * *"   # 每天 00:00 / 08:00 / 16:00 UTC
```

对应北京时间大约 **08:00、16:00、00:00**。  
每天最多成功 4 次，建议不要设超过 3～4 次。  
修改：编辑 `.github/workflows/voer-renew.yml` 里的 `cron`。

### 3.5 失败时的截图

工作流失败时会尝试上传 artifact：

- `debug_screenshot.png`
- `renew_screenshot.png`

在对应 Run 页面 → Artifacts 下载查看。

---

## 四、本地 / VPS 直接运行

### 4.1 安装依赖

```bash
# 系统依赖（Ubuntu / Debian）
sudo apt update
sudo apt install -y xvfb python3-pip

# Python 依赖
pip3 install -r requirements.txt
playwright install chromium
# 系统较老时可加：
# playwright install-deps chromium
```

### 4.2 配置（二选一）

**方式 A：环境变量（推荐，与 GitHub Actions 一致）**

```bash
export VOER_SERVER_ID="你的UUID"
export VOER_TOKEN="你的JWT"
# 可选 TG
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
```

**方式 B：本地 config.json**

```bash
cp config.example.json config.json
# 编辑 config.json，填入 server_id、token，以及可选的 telegram_* 字段
```

### 4.3 运行命令

```bash
# 只看状态（不看广告、不消耗次数）
python3 voer_renew.py --status

# 默认：若关机则开机，然后会话续期（必须带虚拟显示）
xvfb-run -a python3 voer_renew.py

# 只尝试开机（已运行则跳过；开机若需广告会自动看）
xvfb-run -a python3 voer_renew.py --power-on
```

> `headless` 必须为 `false`，且要用 `xvfb-run`。纯无头模式下广告不会发奖励。

---

## 五、定时任务示例

### VPS crontab

```bash
crontab -e
```

示例（每天本地时间 0 / 8 / 16 点）：

```cron
0 0,8,16 * * * cd /root/voer_renew && /usr/bin/xvfb-run -a /usr/bin/python3 voer_renew.py >> renew.log 2>&1
```

建议先手动跑通 `--status` 和一次 `renew`，再挂 cron。

### GitHub Actions

见上文「定时规则」，默认已配置，无需再写 crontab。

---

## 六、常见问题排查

### 1. HTTP 401 Unauthorized

```text
urllib.error.HTTPError: HTTP Error 401: Unauthorized
```

**含义：** API 拒绝了 token，不是脚本逻辑错误。

**处理顺序：**

1. 浏览器确认仍登录 voer.host
2. F12 → Application → Cookies → `token`，**完整重新复制**
3. 更新 GitHub Secret `VOER_TOKEN`（或本地环境变量 / config.json）
4. 先跑 `--status` 验证

新版脚本会打印 token 诊断（长度、段数、是否过期），便于排查复制不全或已过期。

### 2. 找不到「延伸」按钮 / 点击超时

```text
TimeoutError: waiting for get_by_role("button", name="延伸")
```

**含义：** 面板按钮文案与脚本默认不完全一致，或页面未加载完。

新版已兼容多种文案（延伸 / 延长 / 續期 / Extend / Renew 等）。  
若仍失败，日志会打印「可见按钮/链接文字」并保存截图，把列表发出来即可继续适配。

### 3. 关机无法自动开机

脚本根据 API 的 `status` 判断是否关机，并点击「开机 / Start / Power on」等按钮。  
若日志出现「未找到开机按钮」，会打印页面可见按钮列表并截图——把列表发出来可继续适配文案。

开机若弹出与续期相同的激励广告，会自动走同一套「Watch ad → 等待 → Close」流程。

### 4. 「未检测到续期生效」

可能原因：

- 今日已满 **4 次**
- 广告未真正播完
- 页面卡在某个广告上

可把 `VOER_AD_DURATION_SEC` 调大（如 `40`），或查看截图 / Actions 日志。

### 4. 广告不发奖励

必须同时满足：

- `headless: false`
- 使用 `xvfb-run -a ...`（无桌面环境）
- 不要开广告拦截

### 5. 其他

| 现象 | 处理 |
|------|------|
| 404 | 检查 `VOER_SERVER_ID` 是否写错 |
| token 显示「已过期」 | 重新从浏览器复制并更新 |
| JWT 段数不是 3 | 复制不完整，重新复制整段 |
| TG 不通知 | 检查 Bot 是否 Start、Chat ID 是否正确、Secrets 是否配置 |

---

## 七、文件说明

| 文件 | 作用 |
|------|------|
| `voer_renew.py` | 主脚本（环境变量 + config.json，含 TG 通知与截图） |
| `requirements.txt` | Python 依赖（`playwright>=1.40`） |
| `config.example.json` | 本地配置模板（复制为 `config.json`） |
| `.github/workflows/voer-renew.yml` | GitHub Actions：定时 + 手动 status/renew |
| `.gitignore` | 忽略 `config.json`、日志等 |
| `README.md` | 本说明 |

---

## 八、安全建议

1. 仓库尽量设为 **Private**
2. 真实 `token` **只**放在环境变量 / GitHub Secrets / 本地 `config.json`，不要提交到 git
3. token 泄露后：浏览器重新登录一次，旧 token 会失效，再复制新的
4. 定期看 Actions 日志或 TG 通知，确认续期成功
5. token 约 7 天过期，过期前记得更新 Secret

---

## 快速检查清单

首次使用建议按此顺序：

- [ ] 拿到 `VOER_SERVER_ID`（面板 URL 末尾 UUID）
- [ ] 拿到 `VOER_TOKEN`（Cookie 里 name=`token` 的完整 JWT）
- [ ] （可选）创建 TG Bot，拿到 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- [ ] 写入 GitHub Secrets 或本地环境变量
- [ ] 推送本项目全部文件到仓库
- [ ] Actions 先跑 **`status`**，确认能读到到期时间
- [ ] 再跑 **`renew`**，确认日志出现「续期成功」
- [ ] （若配置了 TG）手机收到成功通知和截图
