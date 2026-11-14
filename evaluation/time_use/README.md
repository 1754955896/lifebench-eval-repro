# time_use — 六领域时间利用统计

从 `daily_event.json` 统计每天六类活动的分钟数，汇总日均时长、参与者平均时长、参与率和日间方差。复合事件由 LLM 估计拆分，时间区间计算、跨日切分和总量核算由程序完成。

## 分类口径

| 类别 | 内容 |
|---|---|
| 个人生理必需活动 | 睡眠、休息、个人卫生、用餐、看病就医 |
| 有酬劳动 | 本职工作、工作会议、在家办公、岗位要求的在职培训 |
| 无酬劳动 | 做饭、家务、购物、照料家人/宠物、帮助他人、无酬实习、公益志愿服务 |
| 个人自由支配活动 | 运动健身、摄影、兴趣休闲、娱乐、普通社交 |
| 学习培训 | 课程、备考、自主学习培训 |
| 交通活动 | 为到达目的地的出行，包括通勤、目的性步行 |

以休闲或锻炼为目的的散步、跑步计入自由支配活动。`Career` 等九类事件标签只是参考，不能直接映射到这六类。例如“通勤并买早餐”需要拆成交通、购物和用餐。

六类和“不足15分钟的次要附带活动可归主要活动”的约定来自主仓库历史脚本 `utils/time_use_analysis.py`，提交 `904aef9`，在 `9ce92d8` 删除。本模块同时整合了历史 `utils/hourly_average_analysis.py` 的汇总功能。它是迁移后的修订实现，不保证与历史结果逐数值一致；不是对官方调查微观数据的直接复算。

相对于旧脚本，目的性步行可以计入交通；岗位要求的在职培训计入有酬劳动，自主备考计入学习培训。提示词和版本号保存在 `engine.py`，所有比较应使用相同版本、模型与睡眠策略。

## 输入和每日时间核算

- `--data-dir` 可以是人物目录，或直接包含人物目录的数据集目录。
- 必须有 `daily_event.json`，可选 `persona.json`；只向 LLM 提供职业、爱好和人物描述。
- 处理每条事件 `date` 数组中的**全部区间**，按自然日00:00切分跨日事件。即使只评估某一天，也会读取前一天跨入该日的区间。
- 不修复负时长、倒置日期或零时长，保留输入诊断。选择日期范围时，其他日期的错误不计入本次诊断；无日期可定位的错误仍列出。
- 重叠部分记入 `ambiguous_minutes`，不擅自挑选某条事件，也不发送给模型重复分配；同一事件自身的重叠区间也视为输入歧义。
- 未记录的时间记入 `unobserved_minutes`，不为了凑24小时强行归类。
- 每天遵守：`可分配时间 + 歧义时间 + 未记录时间 = 1440分钟`。
- 模型必须为每个时间片返回六类非负、有限数值，其总和等于该片时长。超过0.01分钟的误差重试，容限以内的舍入差异由程序校正并记录。缺片、重复片、伪造ID、NaN和负数均不接收。

### 睡眠策略

**默认 `--sleep-policy none`：** 只统计明确记录的睡眠；空档保留为未知。它适合审核记录本身，但某些数据只记录“入睡”而未记录整夜睡眠，生理必需时长会低于包含完整夜间睡眠的时间利用调查。

**可选 `--sleep-policy night-gap`：** 将相邻连续日期之间“前一天18:00后结束、次日12:00前开始、空档不超过16小时”的未覆盖部分假设为睡眠。先合并已有覆盖区间，再推算，因此不会重复计算已明确记录的跨夜睡眠。不会跨缺失的整天补觉，也不推算数据首日前和末日后的睡眠，不推算当天凌晨才结束的记录之后的空档。

推算睡眠按自然日分摊，独立标记为 `source=inferred_sleep` 和 `inferred_sleep_minutes`，程序固定归入生理必需，不交给LLM决定。**推算值是分析假设，不是人物实际睡眠的观测值。** 对比不同数据时必须使用相同策略并报告推算量及未知量。

## 运行

### 原始版与两种消融的一键对比

从 `lifebench_data_eval` 目录执行：

```powershell
$env:PYTHONNOUSERSITE='1'
conda run --no-capture-output -n shark python -u evaluation/time_use/run_ablation_comparison.py --max-workers 8
```

默认统计原始冯浩然、去规划版、模拟基线的 **2025-01-01 至 2025-12-31（含首尾365天）**，调用本模块相同的分类提示词与时间核算逻辑。三组顺序运行，每组最多8个并发请求，约1095个每日任务；没有可分配时间的日期不调用模型，失败时最多尝试3次。使用共享模型配置。再次运行相同命令会续跑。

三组对比脚本默认睡眠策略为 `night-gap`，把符合规则的跨夜空档推算为睡眠；仅统计明确记录时可指定 `--sleep-policy none`。两种策略默认使用不同输出目录。可先加 `--dry-run` 检查三组输入，不调用模型、不写文件。单组 `method.py` 的默认值仍为 `none`。

默认输出：`output/time_use/ablation_comparison_20250101_20251231_night-gap/`。`comparison.md` 为六类日均小时对比表及未知、歧义、推算睡眠核算；`comparison.json` 另含参与率、参与日平均时长、日间方差和每组处理状态。各组子目录保留原方法的每日分配明细、模型响应和恢复检查点。

已经跑完 `none` 策略时，可**完全本地补入推算睡眠，不重新调用 LLM**：

```powershell
conda run --no-capture-output -n shark python -u evaluation/time_use/run_ablation_comparison.py --reuse-results-dir output/time_use/ablation_comparison_20250101_20251231_none
```

此模式核对源事件、画像、模型、提示词版本和日期范围，再按原时间片内容映射已有分类（补入睡眠后片段编号可能改变），将推算睡眠固定归入生理必需。不会覆盖源目录，不会重试原先失败日期；完整保留原355个共同成功日期以便前后比较。新目录保存补入睡眠后的每日分配、指标及来源记录，不复制模型响应或创建新的LLM检查点。原先的失败日期仍在失败列表中，退出码可能为1，表示全年仍有缺失而非睡眠补算失败。

对比表统一使用**三组都成功分类的日期交集**；JSON 同时保留各组自身成功日期的汇总与缺失/失败信息。不会把失败日期当作0计入均值。六类时长加未记录、重叠歧义时间才是24小时；推算睡眠已包含在六类中。输入无效区间或重叠会使单组状态为 `incomplete`，不等于请求尚未处理；脚本退出码按是否三组所有日期都成功分类判断。

也可显式指定全年日期：

```powershell
conda run --no-capture-output -n shark python -u evaluation/time_use/run_ablation_comparison.py --start-date 2025-01-01 --end-date 2025-12-31 --max-workers 8
```

日期范围、睡眠策略会自动体现在默认输出目录名中；更换模型应指定新的 `--output-dir`。不要同时启动多个进程写同一目录。

### 单组统计

从独立仓库根目录 `lifebench_data_eval` 运行，使用该仓库的 `config/config.json` 或 `LLM_*` 环境变量，默认调用共享配置的 `default_model`。

```powershell
cd D:\pyCharmProjects\pythonProject4\lifebench_data_eval
$env:PYTHONNOUSERSITE='1'

# 先检查基线全年输入，不调用模型
conda run --no-capture-output -n shark python -m evaluation.time_use.method --data-dir data/ablation_fenghaoran_2025/simulation_baseline --output-dir output/time_use_check/baseline --start-date 2025-01-01 --end-date 2025-12-31 --dry-run

# 先分析一周（独立输出目录）
conda run --no-capture-output -n shark python -m evaluation.time_use.method --data-dir data/ablation_fenghaoran_2025/simulation_baseline --output-dir output/time_use_week/baseline --start-date 2025-07-12 --end-date 2025-07-18 --sleep-policy night-gap --max-workers 8

# 全年统计
conda run --no-capture-output -n shark python -m evaluation.time_use.method --data-dir data/ablation_fenghaoran_2025/simulation_baseline --output-dir output/time_use_year/baseline --start-date 2025-01-01 --end-date 2025-12-31 --sleep-policy night-gap --max-workers 8
```

原始版输入为 `data/lifebench_10people/fenghaoran`，去规划版为 `data/ablation_fenghaoran_2025/no_planning`，为各组指定不同的输出目录。一个输出目录只承载同一输入、日期范围、模型和统计策略。

每天有记录的可分配片段时通常调用一次模型；推算睡眠由程序直接分配，没有片段的日期不调用模型。失败或输出不满足时长核算时，最多尝试 `--attempts 3` 次。`--timeout` 默认180秒，关闭SDK隐式重试。出现余额不足/鉴权失败后，本人物剩余待执行任务停止发请求，正在运行的请求可能继续完成。

重跑同一命令自动复用成功日期，只重试失败日期。检查点包含模型和输入签名；更换输入/模型/日期范围时会拒绝混用，建议使用新输出目录。`--no-resume` 可明确重新计算覆盖当前选择日期。运行中打印完成进度，模型等待超过30秒会打印等待提示。

统一API，允许离线注入 `client.chat(messages, max_tokens)`，返回含 `content`、`finish_reason` 的字典：

```python
from evaluation.time_use.method import evaluate

result = evaluate(
    "data/ablation_fenghaoran_2025/simulation_baseline",
    "output/time_use_year/baseline",
    options={"start_date": "2025-01-01", "end_date": "2025-12-31",
             "sleep_policy": "night-gap", "max_workers": 8},
)
```

## 输出及指标定义

```text
<output_dir>/metrics.json
<output_dir>/<person>/
  metrics.json           # 汇总、输入诊断、模型/提示词/源文件签名
  manifest.json
  daily_results.json     # 日期→六类分钟数、事件计数、分配明细、未知和歧义时间
  input_accounting.json  # 所有选定日期的未知/歧义等核算，包括没有可分类记录的日期
  days/<date>.json       # 每日成功检查点
  responses/*.json       # 模型原始响应和用量（若客户端提供）
  failures.json
```

`--dry-run` 保存 `plan.json`、`prompt_preview.json` 和诊断指标，不调用LLM。建议使用独立的检查目录。

`category_distribution` 中：

- `daily_average_hours/minutes`：类别总时长除以**成功分析日期数**，包括该类为0的日期。
- `participation_average_hours`：类别总时长除以该类时长大于0的日期数；没人参与时为null。
- `participation_rate`：参与日期数除以成功分析日期数，范围0—1。
- `daily_variance_minutes`：成功日期日时长的样本方差，单位分钟平方；一个日期时记0。
- `percentage_of_allocated_time`：占已分配六类总时长的百分比，分母不是固定24小时。
- `event_count`：各日涉及该类的不同源事件数之和；同一源事件的多个时段在同一天不重复计数，跨日可在各日出现。

根指标只合计各类分钟和已分析人日，不简单平均不同人物的比例。

存在失败、选定范围的无效区间或重叠时，状态为 `incomplete`、CLI退出码为1，已经完成的非歧义部分仍会保留。完全没有可分配时间的日期也记入失败/未完成列表，不能当成六类活动都为0的有效观测。`complete` 表示处理完成，不表示全天1440分钟均有事实记录；必须同时查看未知分钟。失败日期不以0补进均值，需报告 `analyzed_days/requested_days`。`accounting_on_requested_days` 覆盖全部选定日期，`totals_on_analyzed_days` 仅覆盖已成功分类日期。

## 限制

复合事件内缺少明确分时的信息时，分类时长仍是模型估计；不同模型或采样可能产生差异。当前不提供相对于全国调查的真实性分数，也不会把未知时间全部解释为睡眠。多个进程不要同时写同一输出目录。
