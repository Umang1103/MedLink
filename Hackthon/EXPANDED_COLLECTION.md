# 扩展药品采集

输入：`heart_failure_collection_list_expanded.csv`（188 个不同 PZN）。

输出：`medicines.csv`、`medicines.xlsx`，共 190 条；新增 174 条，原有 16 条的非空字段保留。原表另有 2 条不在扩展清单内，仍然保留。

每个 PZN 在项目根目录下使用 8 位目录名，与 `01015676` 示例相同。新增下载的命名：

- `<PZN>_tablet_1.jpg`、`<PZN>_tablet_2.jpg`（有第二张时）；PNG 保留 PNG 扩展名。
- `<PZN>_leaflet.pdf`（来源网页有提供时）。
- `raw_extract.json`：网页提取结果、下载 URL、文件类型、SHA-256 和问题记录。
- `sources.json`：字段来源及原清单信息。
- `leaflet_text.txt`：下载说明书的提取原文。

清单内 186 个 PZN 有药片图片、169 个有 PDF，合计 337 张药片图片。19 个网页没有链接说明书 PDF，其中 `01363153` 和 `17420746` 也没有链接药片原图；这两个页面仅下载了单独的包装图片，没有将其当作药片照片。缺项参见 `expanded_collection_status.csv` 和 `expanded_collection_validation.json`。

新增表格列保留分类、心衰背景、作用机制、临床角色、规格说明、供应企业原始字段及资料链接。它们来自输入清单，不代表已核验各具体产品的心衰适应证。

颜色、形状、刻印和企业信息来自说明书的自动提取；不能明确区分多个规格时留空。网页供应企业单列记录，不自动当作实际生产厂家。新增条目未经过逐项目视或药师审核。

运行完整下载及合并（可重复运行）：

```powershell
python collect_expanded.py --workers 4
```

仅从本地提取文件重建表格：

```powershell
python collect_expanded.py --build-only
```

脚本每次写表格前会备份 CSV 和 XLSX。首次原始备份：`backups/20261113_164155/`。原有采集脚本仍读取旧清单；扩展清单请使用 `collect_expanded.py`。

本次验证：两份表格内容一致；PZN 为 8 位文本；原有非空字段保持不变；全部引用 PDF 可解析、图片可解码；所有清单 PZN 均有目录和来源记录。下载内容的再使用许可沿用原项目记录，尚未核实。
