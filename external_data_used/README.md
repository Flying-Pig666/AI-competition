# 吉隆泥石流研究：已使用外部数据整理包

生成时间（UTC）：2026-10-25T07:00:15Z

## 范围
本包仅收录建模流程中已经采用或明确用于交叉核验的外部公开数据；不重复收录赛题官方附件。

## 数据目录
1. `01_ERA5_reanalysis/`：吉隆口岸附近和尼泊尔近源区的 ERA5 小时再分析，2026-08-20—2026-08-27。
2. `02_ECMWF_IFS_previous_runs/`：ECMWF IFS 在有效时刻 2026-08-26 的提前 1—7 天固定提前量预报；含全天表和 08:00—18:00 表。
3. `03_ERA5_Land_soil/`：吉隆事件点 ERA5-Land 多层土壤温度、体积含水量小时表及日汇总，2026-08-20—2026-08-26。
4. `04_NASA_POWER_validation/`：NASA POWER 小时点数据，用于独立交叉核验；源时间为 UTC，并新增北京时间列。
5. `05_manifest_and_reproducibility/`：数据清单、来源 URL、检索结果、SHA-256 校验值和本重建脚本。

## 使用原则
- ERA5/ERA5-Land 是格点再分析，不能表述为地面站实测。
- ECMWF `previous_day1` 至 `previous_day7` 是固定提前量预报情景，不能直接当作 7 个独立样本或 95% 置信区间。
- NASA POWER 只作独立核验，不与 ERA5 简单平均。
- 若历史 ECMWF 集合均值/离散度无法从公开接口可靠重建，本包只保留失败状态说明，不生成合成或估算数据。
- 事件时刻 10:30 与小时资料不在同一分辨率；小时资料支持的时间约束是 `[10:00, 11:00)`。

## 许可与署名
- Open-Meteo API data: CC BY 4.0，使用时注明 Open-Meteo 及底层模型来源。
- NASA POWER: 使用时注明 NASA POWER Project / NASA Langley Research Center。

## 任务状态
成功：8 项；失败：0 项。详细信息见 `retrieval_results.json`。
