# 掺烧试验中的证据放行与运行边界

此目录保存掺竹燃料替代试验的合成业务资料、字段契约、资料包自检工具，
以及建立在资料之上的分析产品 `cofire`：从事先登记、逐轮查看、下一轮
判断到运行交接。资料说明见 docs/资料口径.md，产品口径与使用说明见
docs/产品说明.md，文件目录及完整性摘要见 data/catalog.json。

全部主体和观测均为本地合成资料；产品不连接、不控制任何真实装置。

## 环境与资料检查

使用 Python 3.11.2，依赖在 requirements.txt 中锁定。可在虚拟环境安装：

    python3 -m pip install -r requirements.txt
    python3 -m sourcepack

资料检查逐文件回报行数、键唯一性、完整性与关系引用是否符合资料目录；
资料行中的拒绝、停试、缺测和不确定范围是业务记录，不是读入错误。

## 分析产品 cofire

    python3 -m cofire init   /tmp/ws --pack .   # 建工作区，冻结登记
    python3 -m cofire look   /tmp/ws --day 8    # 按登记查看日执行查看
    python3 -m cofire look   /tmp/ws --day 16
    python3 -m cofire look   /tmp/ws --day 24
    python3 -m cofire decide /tmp/ws            # 下一轮判断
    python3 -m cofire synth-round2 /tmp/round2  # 生成合成第二轮（演示）
    python3 -m cofire ingest /tmp/ws --pack /tmp/round2
    python3 -m cofire decide /tmp/ws            # 新证据下的判断
    python3 -m cofire why    /tmp/ws N1         # 回查建议为何变化
    python3 -m cofire handoff /tmp/ws -o handoff.md

班次是独立分析单位，测点只在班次内聚合；显著性只在登记查看日按
O'Brien-Fleming 消耗解释；停试班次保留在原定方案口径内并单列不可识别
部分；没有证据支持的工况不得用平均安全外推。完整口径见 docs/产品说明.md。

## 测试与语法构建

    python3 -m unittest discover -s tests -v
    python3 -m compileall -q sourcepack cofire tests

测试覆盖资料检查器、班次聚合与防测点虚增、登记冻结与修订失效、序贯
查看、三种估计口径、下一轮判断规则、两轮端到端流程与交接报告内容。
运行无需访问外部业务系统。
