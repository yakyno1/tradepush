# TradePush

本机 A股 / 港股规则化交易控制台。

```text
市场开关 → 板块轮动 → 多周期预测 → 个股硬门槛 → 仓位风控 → 人工执行
```

## 打开应用

```powershell
cd E:\Ai_project\tradepush
.\RUN_TRADEPUSH.ps1
```

访问：<http://localhost:8510>

## 本轮预测体系

板块和个股统一提供：

- 一周：5个交易日。
- 一个月：20个交易日。
- 三个月：60个交易日。
- 置信度：衡量历史覆盖、相似样本、字段完整度和数据新鲜度。
- 模型自信度：衡量动量、趋势、量能、市场和板块信号的一致程度。

硬门槛：

```text
置信度 < 60 或模型自信度 < 55 → 分析不出结果
```

系统不会把低质量证据包装成方向预测。个股区间来自历史相似状态的分位数，不是收益保证。

## 页面钻取

- 板块轮动：点击板块行，查看一周/一个月/三个月预测、资金价格历史、因子贡献和对应股票。
- 个股决策：点击股票行，查看K线、预测区间、历史相似样本、置信度、自信度、原始数据和错误诊断。
- 数据中心：查看每个周期有多少股票/板块达到预测门槛。
- AI复核：分析包包含候选股票和板块的多周期预测；AI不得升级“分析不出结果”。

## 本地独立采集

项目运行时只读取当前目录下的 `config/`、`data/` 和 Cookie 文件。

```powershell
# 盘中：雪球行情 + 东方财富板块
.\.venv\Scripts\python.exe collect_data.py intraday

# 行情、板块和雪球历史K线
.\.venv\Scripts\python.exe collect_data.py all

# AKShare A/H 历史K线
.\.venv\Scripts\python.exe collect_data.py history

# 补录单个历史交易日
.\.venv\Scripts\python.exe collect_data.py reconstruct 2026-06-22

# 同时联网补回东方财富历史板块涨跌和主力资金
.\.venv\Scripts\python.exe collect_data.py reconstruct 2026-06-22 --force --with-sector-history

# 批量补录日期区间（自动跳过周末和休市日）
.\.venv\Scripts\python.exe collect_data.py reconstruct-range --start 2026-06-01 --end 2026-06-22

# 已有重建版时仍保留一个新的补算版本
.\.venv\Scripts\python.exe collect_data.py reconstruct 2026-06-22 --force

# PowerShell 快捷脚本：一个日期为单日，两个日期为区间
.\BACKFILL_HISTORY.ps1 2026-06-22
.\BACKFILL_HISTORY.ps1 2026-06-01 2026-06-22
```

历史补录会从本地历史K线提取当日股票和指数收盘数据，并只使用截至该日的数据补算规则与预测。
未曾保存的板块资金、盘中快照和当时账户配置无法事后精确恢复，系统会明确标记缺失或当前配置，
不会拿其他日期的数据冒充。

Cookie：

```text
xueqiu_cookie.txt
eastmoney_cookie.txt
```

它们已被 `.gitignore` 排除。页面只显示状态，不显示内容；失败采集不会覆盖上一份可用行情。

> TradePush 不连接券商，不自动下单。所有预测都是条件判断，必须结合确认条件、失效条件和账户风险人工执行。
