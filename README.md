# TradePush

本机 A股 / 港股规则化交易控制台。核心链路：

```text
市场开关 → 板块轮动与1–3日条件前瞻 → 个股硬门槛 → 仓位风控 → 买/等/卖
```

## 打开应用

在 PowerShell 运行：

```powershell
cd E:\Ai_project\tradepush
.\RUN_TRADEPUSH.ps1
```

首次运行会在本目录创建 `.venv` 并安装依赖。浏览器访问：

```text
http://localhost:8510
```

## 本地独立采集

项目运行时只读取当前目录下的 `config/`、`data/` 和两个 Cookie 文件，不再读取其他工程。

```powershell
# 盘中：雪球行情 + 东方财富板块
.\.venv\Scripts\python.exe collect_data.py intraday

# 完整：行情、板块、雪球历史K线
.\.venv\Scripts\python.exe collect_data.py all

# AKShare A/H 历史K线
.\.venv\Scripts\python.exe collect_data.py history
```

也可在“数据中心与设置”页面点击采集按钮。失败采集不会覆盖上一份可用行情。

Cookie 文件：

```text
xueqiu_cookie.txt
eastmoney_cookie.txt
```

它们已被 `.gitignore` 排除，页面只显示状态，不显示内容。

## 使用顺序

1. 数据中心运行采集并检查数据日期。
2. 今日总览读取结论与五层推导过程。
3. 板块轮动验证前瞻候选的确认/失效条件。
4. 个股决策检查触发价、失效位、目标与硬否决。
5. 持仓与风控核对股数和组合风险。
6. 人工确认交易；系统不连接券商自动下单。

> 板块前瞻是条件判断，不是收益承诺。数据过期、确认条件未发生或失效条件触发时，执行“等待/回避”。
